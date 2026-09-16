//
//  GatewayPromptIngress.swift
//  MinisApp
//
//  Fail-closed loopback HTTP handler for POST /gateway/prompt.
//
//  Production Open Minis 1.14 blanket-rejects this path (GET not_ready /
//  POST 409 tool-only). This file restores a SINGLE unlock: an explicit
//  lingxin HANDOFF marker, behind a kill-switch that defaults OFF.
//
//  Scope (do not expand):
//    • Empty/missing session → NEW chat via SessionsOffloadBridge.sendPrompt
//    • Never lock to a fixed Session
//    • No Shortcuts / Pushcut / Focus primary path
//    • No second autonomy wake chain, no AFC writeback, no schedulers
//
//  Kill-switch (BOTH must be considered; either one enables):
//    • UserDefaults key `gatewayPromptHandoffEnabled` (bool, default false)
//    • Env `MINIS_GATEWAY_PROMPT_HANDOFF` = 1|true|yes|on
//
//  Optional: `MINIS_GATEWAY_PROMPT_PORT` overrides listen port (default 8314).
//
//  Mac → iOS (USB/iproxy, loopback only):
//    iproxy 8314 8314
//    curl -sS -X POST http://127.0.0.1:8314/gateway/prompt \
//      -H 'Content-Type: application/json' \
//      -H 'Idempotency-Key: <uuid>' \
//      -H 'X-Minis-Source: lingxin-handoff' \
//      -d '{"prompt":"hello","source":"lingxin-handoff","session":""}'
//

import Darwin
import Foundation

private let logger = AppLogger(category: "GatewayPromptIngress")

// MARK: - HANDOFF gate (begin extract)

/// Pure allowlist for `/gateway/prompt`. No I/O. Fail-closed.
enum GatewayPromptHandoffGate {
    static let allowedSource = "lingxin-handoff"
    static let path = "/gateway/prompt"
    static let defaultPort: UInt16 = 8314
    static let killSwitchDefaultsKey = "gatewayPromptHandoffEnabled"
    static let killSwitchEnvKey = "MINIS_GATEWAY_PROMPT_HANDOFF"
    static let portEnvKey = "MINIS_GATEWAY_PROMPT_PORT"
    static let minIdempotencyLength = 8
    static let maxIdempotencyLength = 256

    struct Decision: Equatable {
        var allow: Bool
        var httpStatus: Int
        var statusText: String
        var json: [String: Any]

        static func == (lhs: Decision, rhs: Decision) -> Bool {
            lhs.allow == rhs.allow
                && lhs.httpStatus == rhs.httpStatus
                && lhs.statusText == rhs.statusText
                && NSDictionary(dictionary: lhs.json).isEqual(to: rhs.json)
        }
    }

    static func isKillSwitchOn(
        env: [String: String] = ProcessInfo.processInfo.environment,
        defaultsEnabled: Bool? = nil
    ) -> Bool {
        let defaultsOn = defaultsEnabled
            ?? UserDefaults.standard.bool(forKey: killSwitchDefaultsKey)
        if defaultsOn { return true }
        let raw = (env[killSwitchEnvKey] ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
        return raw == "1" || raw == "true" || raw == "yes" || raw == "on"
    }

    static func listenPort(
        env: [String: String] = ProcessInfo.processInfo.environment
    ) -> UInt16 {
        guard let raw = env[portEnvKey]?.trimmingCharacters(in: .whitespacesAndNewlines),
              let parsed = UInt16(raw), parsed > 0 else {
            return defaultPort
        }
        return parsed
    }

    static func normalizePath(_ path: String) -> String {
        var p = path
        if let q = p.firstIndex(of: "?") {
            p = String(p[..<q])
        }
        while p.count > 1 && p.hasSuffix("/") {
            p.removeLast()
        }
        return p
    }

    static func header(_ headers: [String: String], _ name: String) -> String? {
        let want = name.lowercased()
        for (k, v) in headers where k.lowercased() == want {
            let trimmed = v.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty { return trimmed }
        }
        return nil
    }

    static func resolveSource(
        headers: [String: String],
        query: [String: String],
        body: [String: Any]
    ) -> String {
        if let s = stringValue(body["source"]), !s.isEmpty { return s }
        if let s = header(headers, "X-Minis-Source") { return s }
        if let s = header(headers, "X-Handoff-Source") { return s }
        if let s = query["source"], !s.isEmpty { return s }
        return ""
    }

    static func resolveIdempotencyKey(
        headers: [String: String],
        body: [String: Any]
    ) -> String {
        if let s = header(headers, "Idempotency-Key") { return s }
        if let s = header(headers, "X-Idempotency-Key") { return s }
        if let s = stringValue(body["idempotencyKey"]), !s.isEmpty { return s }
        if let s = stringValue(body["idempotency_key"]), !s.isEmpty { return s }
        return ""
    }

    static func resolvePrompt(body: [String: Any]) -> String {
        if let s = stringValue(body["prompt"]) { return s }
        if let s = stringValue(body["message"]) { return s }
        if let s = stringValue(body["text"]) { return s }
        return ""
    }

    /// Session ids are ignored: HANDOFF always opens a NEW chat.
    static func resolveSessionId(body: [String: Any]) -> String? {
        _ = body["session"] ?? body["sessionId"] ?? body["session_id"]
        return nil
    }

    static func stringValue(_ any: Any?) -> String? {
        if let s = any as? String {
            let t = s.trimmingCharacters(in: .whitespacesAndNewlines)
            return t
        }
        if let n = any as? NSNumber {
            return n.stringValue
        }
        return nil
    }

    static func evaluate(
        method: String,
        path: String,
        headers: [String: String],
        query: [String: String],
        body: [String: Any],
        killSwitchOn: Bool
    ) -> Decision {
        if normalizePath(path) != Self.path {
            return Decision(
                allow: false,
                httpStatus: 404,
                statusText: "404 Not Found",
                json: ["ok": false, "error": "not_found"]
            )
        }

        let verb = method.uppercased()
        if verb == "GET" {
            // Current production reject. GET never unlocks, even with kill-switch.
            return Decision(
                allow: false,
                httpStatus: 503,
                statusText: "503 Service Unavailable",
                json: ["ok": false, "status": "not_ready"]
            )
        }
        if verb != "POST" {
            return Decision(
                allow: false,
                httpStatus: 405,
                statusText: "405 Method Not Allowed",
                json: ["ok": false, "error": "method_not_allowed"]
            )
        }

        // POST: default-deny looks identical to production (409 tool-only)
        // until BOTH kill-switch AND source allowlist pass.
        if !killSwitchOn {
            return toolOnly()
        }
        let source = resolveSource(headers: headers, query: query, body: body)
        if source != allowedSource {
            return toolOnly()
        }
        let key = resolveIdempotencyKey(headers: headers, body: body)
        if key.count < minIdempotencyLength || key.count > maxIdempotencyLength {
            return Decision(
                allow: false,
                httpStatus: 400,
                statusText: "400 Bad Request",
                json: ["ok": false, "error": "idempotency_key_required"]
            )
        }
        let prompt = resolvePrompt(body: body)
        if prompt.isEmpty {
            return Decision(
                allow: false,
                httpStatus: 400,
                statusText: "400 Bad Request",
                json: ["ok": false, "error": "empty_prompt"]
            )
        }
        return Decision(
            allow: true,
            httpStatus: 200,
            statusText: "200 OK",
            json: [
                "ok": true,
                "status": "accepted",
                "source": allowedSource,
                "idempotency_key": key,
                "prompt": prompt,
                "new_chat": true,
            ]
        )
    }

    private static func toolOnly() -> Decision {
        Decision(
            allow: false,
            httpStatus: 409,
            statusText: "409 Conflict",
            json: ["ok": false, "error": "tool-only"]
        )
    }
}

// MARK: - HANDOFF gate (end extract)

// MARK: - Loopback HTTP server

/// Loopback-only listener for `/gateway/prompt`. Bind is 127.0.0.1 so LAN
/// cannot hit this path; Mac reaches it via USB iproxy.
final class GatewayPromptIngressServer: @unchecked Sendable {
    static let shared = GatewayPromptIngressServer()

    private let acceptQueue = DispatchQueue(label: "minis.gateway.prompt.accept")
    private let dispatchQueue = DispatchQueue(label: "minis.gateway.prompt.dispatch")
    private let cacheLock = NSLock()
    private var listenSocket: Int32 = -1
    private var stopped = false
    private var idempotencyCache: [String: (status: String, body: String)] = [:]
    private var idempotencyOrder: [String] = []
    private let idempotencyCap = 64

    /// Overridable for tests. Default talks to SessionsOffloadBridge.
    var sendPromptImpl: (String) -> [String: Any] = { prompt in
        let result = SessionsOffloadBridge.sendPrompt(
            sessionId: nil,
            prompt: prompt,
            attachmentPaths: [],
            modelEntryId: nil,
            source: GatewayPromptHandoffGate.allowedSource
        )
        return (result as? [String: Any]) ?? ["ok": false, "error": "dispatch_failed"]
    }

    func start(port: UInt16? = nil) {
        let bindPort = port ?? GatewayPromptHandoffGate.listenPort()
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else {
            logger.error("[GatewayPromptIngress] socket() failed")
            return
        }

        var reuse: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &reuse, socklen_t(MemoryLayout<Int32>.size))

        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = bindPort.bigEndian
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")

        let bindResult = withUnsafePointer(to: &addr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockPtr in
                bind(fd, sockPtr, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard bindResult == 0 else {
            close(fd)
            logger.error("[GatewayPromptIngress] bind 127.0.0.1:\(bindPort) failed errno=\(errno)")
            return
        }
        guard listen(fd, 5) == 0 else {
            close(fd)
            logger.error("[GatewayPromptIngress] listen failed errno=\(errno)")
            return
        }

        self.listenSocket = fd
        self.stopped = false
        logger.info("[GatewayPromptIngress] loopback :\(bindPort) /gateway/prompt (handoff kill-switch default OFF)")
        acceptQueue.async { [weak self] in
            self?.acceptLoop()
        }
    }

    func stop() {
        stopped = true
        if listenSocket >= 0 {
            close(listenSocket)
            listenSocket = -1
        }
    }

    /// Same foreground rebuild as DebugServer: iOS can tear down the listen
    /// queue across a long background. Unconditional re-bind; SO_REUSEADDR is set.
    func restartIfDead(port: UInt16? = nil) {
        if listenSocket >= 0 {
            close(listenSocket)
            listenSocket = -1
        }
        stopped = false
        start(port: port)
    }

    // MARK: Private

    private func acceptLoop() {
        while !stopped && listenSocket >= 0 {
            var clientAddr = sockaddr_in()
            var addrLen = socklen_t(MemoryLayout<sockaddr_in>.size)
            let clientFd = withUnsafeMutablePointer(to: &clientAddr) { ptr in
                ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockPtr in
                    accept(listenSocket, sockPtr, &addrLen)
                }
            }
            if clientFd < 0 {
                if stopped { break }
                let err = errno
                if err == EBADF || err == EINVAL || err == ENOTSOCK { break }
                if err != EINTR { usleep(50_000) }
                continue
            }
            let fd = clientFd
            acceptQueue.async { [weak self] in
                self?.handleConnection(fd)
            }
        }
    }

    private func handleConnection(_ fd: Int32) {
        guard let parsed = readHTTPRequest(fd) else {
            sendResponse(fd, status: "400 Bad Request",
                         body: jsonString(["ok": false, "error": "parse_error"]))
            close(fd)
            return
        }
        let (method, rawPath, headers, bodyText) = parsed
        let (path, query) = splitQuery(rawPath)
        let bodyObj = parseJSONObject(bodyText)
        let kill = GatewayPromptHandoffGate.isKillSwitchOn()
        let decision = GatewayPromptHandoffGate.evaluate(
            method: method,
            path: path,
            headers: headers,
            query: query,
            body: bodyObj,
            killSwitchOn: kill
        )

        if !decision.allow {
            sendResponse(fd, status: decision.statusText, body: jsonString(decision.json))
            close(fd)
            return
        }

        let key = GatewayPromptHandoffGate.resolveIdempotencyKey(headers: headers, body: bodyObj)
        if let cached = cachedResponse(for: key) {
            sendResponse(fd, status: cached.status, body: cached.body)
            close(fd)
            return
        }

        let prompt = GatewayPromptHandoffGate.resolvePrompt(body: bodyObj)
        // sendPrompt blocks on a semaphore and MUST NOT run on main.
        let dispatched: [String: Any] = dispatchQueue.sync {
            sendPromptImpl(prompt)
        }
        var payload = dispatched
        payload["handoff"] = true
        payload["new_chat"] = true
        payload["source"] = GatewayPromptHandoffGate.allowedSource
        payload["idempotency_key"] = key
        let body = jsonString(payload)
        rememberResponse(key: key, status: "200 OK", body: body)
        sendResponse(fd, status: "200 OK", body: body)
        close(fd)
    }

    private func cachedResponse(for key: String) -> (status: String, body: String)? {
        cacheLock.lock()
        defer { cacheLock.unlock() }
        return idempotencyCache[key]
    }

    private func rememberResponse(key: String, status: String, body: String) {
        cacheLock.lock()
        defer { cacheLock.unlock() }
        if idempotencyCache[key] == nil {
            idempotencyOrder.append(key)
            if idempotencyOrder.count > idempotencyCap {
                let drop = idempotencyOrder.removeFirst()
                idempotencyCache.removeValue(forKey: drop)
            }
        }
        idempotencyCache[key] = (status, body)
    }

    private func splitQuery(_ raw: String) -> (String, [String: String]) {
        guard let qIndex = raw.firstIndex(of: "?") else { return (raw, [:]) }
        let path = String(raw[..<qIndex])
        let qs = String(raw[raw.index(after: qIndex)...])
        var out: [String: String] = [:]
        for pair in qs.split(separator: "&") {
            let parts = pair.split(separator: "=", maxSplits: 1).map(String.init)
            guard let k = parts.first, !k.isEmpty else { continue }
            let v = parts.count > 1 ? decodeQuery(parts[1]) : ""
            out[k] = v
        }
        return (path, out)
    }

    private func decodeQuery(_ s: String) -> String {
        s.replacingOccurrences(of: "+", with: " ")
            .removingPercentEncoding ?? s
    }

    private func parseJSONObject(_ text: String) -> [String: Any] {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty,
              let data = trimmed.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return [:]
        }
        return obj
    }

    private func jsonString(_ obj: [String: Any]) -> String {
        let data = (try? JSONSerialization.data(withJSONObject: obj, options: [])) ?? Data("{}".utf8)
        return String(data: data, encoding: .utf8) ?? "{}"
    }

    private func readHTTPRequest(_ fd: Int32) -> (method: String, path: String, headers: [String: String], body: String)? {
        var headerData = Data()
        var buffer = [UInt8](repeating: 0, count: 1)
        while headerData.count < 16384 {
            let n = read(fd, &buffer, 1)
            guard n == 1 else { return nil }
            headerData.append(buffer[0])
            if headerData.count >= 4,
               headerData.suffix(4).elementsEqual([0x0D, 0x0A, 0x0D, 0x0A]) {
                break
            }
        }
        guard let headerString = String(data: headerData, encoding: .utf8),
              let firstLine = headerString.split(separator: "\r\n").first else {
            return nil
        }
        let parts = firstLine.split(separator: " ")
        guard parts.count >= 2 else { return nil }
        let method = String(parts[0])
        let path = String(parts[1])

        var contentLength = 0
        var headers: [String: String] = [:]
        for line in headerString.split(separator: "\r\n").dropFirst() {
            guard let colon = line.firstIndex(of: ":") else { continue }
            let name = String(line[..<colon]).trimmingCharacters(in: .whitespaces)
            let value = String(line[line.index(after: colon)...]).trimmingCharacters(in: .whitespaces)
            if name.isEmpty { continue }
            headers[name] = value
            if name.lowercased() == "content-length" {
                contentLength = Int(value) ?? 0
            }
        }
        contentLength = min(max(contentLength, 0), 1_000_000)

        var body = ""
        if contentLength > 0 {
            var bodyBuffer = [UInt8](repeating: 0, count: contentLength)
            var totalRead = 0
            while totalRead < contentLength {
                let n = bodyBuffer.withUnsafeMutableBufferPointer { ptr in
                    read(fd, ptr.baseAddress! + totalRead, contentLength - totalRead)
                }
                guard n > 0 else { break }
                totalRead += n
            }
            body = String(bytes: bodyBuffer[0..<totalRead], encoding: .utf8) ?? ""
        }
        return (method, path, headers, body)
    }

    private func sendResponse(_ fd: Int32, status: String, body: String) {
        let bodyBytes = Array(body.utf8)
        let header = "HTTP/1.1 \(status)\r\nContent-Type: application/json\r\nContent-Length: \(bodyBytes.count)\r\nConnection: close\r\n\r\n"
        var out = Array(header.utf8)
        out.append(contentsOf: bodyBytes)
        var offset = 0
        out.withUnsafeBytes { buf in
            while offset < buf.count {
                let n = write(fd, buf.baseAddress!.advanced(by: offset), buf.count - offset)
                if n <= 0 { break }
                offset += n
            }
        }
    }
}
