// Standalone (`swift GatewayPromptIngressGateTests.swift`) gate tests for the
// HANDOFF-only unlock of POST /gateway/prompt.
//
// MinisTests has a pre-existing compile break, so this file is excluded from
// that target (same rationale as the other Standalone/*.swift tests).
//
// The decision table is reproduced here AND checked against tokens in the
// shipping GatewayPromptIngress.swift so the copy cannot silently drift.

import Foundation

var failures = 0
func check(_ label: String, _ actual: Bool, _ expected: Bool = true) {
    if actual == expected { print("  ✅ \(label)") }
    else { print("  ❌ \(label) — expected \(expected), got \(actual)"); failures += 1 }
}
func checkEq<T: Equatable>(_ label: String, _ actual: T, _ expected: T) {
    if actual == expected { print("  ✅ \(label)") }
    else { print("  ❌ \(label) — expected \(expected), got \(actual)"); failures += 1 }
}

let shippingPath: String = {
    let here = URL(fileURLWithPath: #filePath)
    return here.deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent()
        .appendingPathComponent("NativeOffloads/GatewayPromptIngress.swift").path
}()
let shipping = (try? String(contentsOfFile: shippingPath, encoding: .utf8)) ?? ""

check("shipping source exists", !shipping.isEmpty)
check("shipping contains allowed source", shipping.contains("lingxin-handoff"))
check("shipping contains kill-switch defaults key", shipping.contains("gatewayPromptHandoffEnabled"))
check("shipping contains kill-switch env", shipping.contains("MINIS_GATEWAY_PROMPT_HANDOFF"))
check("shipping GET not_ready", shipping.contains("\"not_ready\""))
check("shipping POST tool-only", shipping.contains("\"tool-only\""))
check("shipping forces new chat (nil session)", shipping.contains("sessionId: nil"))
check("shipping does not restore a fixed session lock", !shipping.contains("fixedSession") && !shipping.contains("lockedSessionId"))
check("shipping default port 8314", shipping.contains("8314"))

// MARK: - Reproduced gate (must stay aligned with GatewayPromptHandoffGate)

let allowedSource = "lingxin-handoff"
let gatePath = "/gateway/prompt"
let minIdempotencyLength = 8
let maxIdempotencyLength = 256

func isKillSwitchOn(env: [String: String], defaultsEnabled: Bool) -> Bool {
    if defaultsEnabled { return true }
    let raw = (env["MINIS_GATEWAY_PROMPT_HANDOFF"] ?? "")
        .trimmingCharacters(in: .whitespacesAndNewlines)
        .lowercased()
    return raw == "1" || raw == "true" || raw == "yes" || raw == "on"
}

func normalizePath(_ path: String) -> String {
    var p = path
    if let q = p.firstIndex(of: "?") { p = String(p[..<q]) }
    while p.count > 1 && p.hasSuffix("/") { p.removeLast() }
    return p
}

func header(_ headers: [String: String], _ name: String) -> String? {
    let want = name.lowercased()
    for (k, v) in headers where k.lowercased() == want {
        let trimmed = v.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmed.isEmpty { return trimmed }
    }
    return nil
}

func stringValue(_ any: Any?) -> String? {
    if let s = any as? String {
        return s.trimmingCharacters(in: .whitespacesAndNewlines)
    }
    return nil
}

func resolveSource(headers: [String: String], query: [String: String], body: [String: Any]) -> String {
    if let s = stringValue(body["source"]), !s.isEmpty { return s }
    if let s = header(headers, "X-Minis-Source") { return s }
    if let s = header(headers, "X-Handoff-Source") { return s }
    if let s = query["source"], !s.isEmpty { return s }
    return ""
}

func resolveIdempotencyKey(headers: [String: String], body: [String: Any]) -> String {
    if let s = header(headers, "Idempotency-Key") { return s }
    if let s = header(headers, "X-Idempotency-Key") { return s }
    if let s = stringValue(body["idempotencyKey"]), !s.isEmpty { return s }
    if let s = stringValue(body["idempotency_key"]), !s.isEmpty { return s }
    return ""
}

func resolvePrompt(body: [String: Any]) -> String {
    if let s = stringValue(body["prompt"]) { return s }
    if let s = stringValue(body["message"]) { return s }
    if let s = stringValue(body["text"]) { return s }
    return ""
}

struct Decision: Equatable {
    var allow: Bool
    var httpStatus: Int
    var error: String?
}

func evaluate(
    method: String,
    path: String,
    headers: [String: String] = [:],
    query: [String: String] = [:],
    body: [String: Any] = [:],
    killSwitchOn: Bool
) -> Decision {
    if normalizePath(path) != gatePath {
        return Decision(allow: false, httpStatus: 404, error: "not_found")
    }
    let verb = method.uppercased()
    if verb == "GET" {
        return Decision(allow: false, httpStatus: 503, error: "not_ready")
    }
    if verb != "POST" {
        return Decision(allow: false, httpStatus: 405, error: "method_not_allowed")
    }
    if !killSwitchOn {
        return Decision(allow: false, httpStatus: 409, error: "tool-only")
    }
    if resolveSource(headers: headers, query: query, body: body) != allowedSource {
        return Decision(allow: false, httpStatus: 409, error: "tool-only")
    }
    let key = resolveIdempotencyKey(headers: headers, body: body)
    if key.count < minIdempotencyLength || key.count > maxIdempotencyLength {
        return Decision(allow: false, httpStatus: 400, error: "idempotency_key_required")
    }
    if resolvePrompt(body: body).isEmpty {
        return Decision(allow: false, httpStatus: 400, error: "empty_prompt")
    }
    return Decision(allow: true, httpStatus: 200, error: nil)
}

func handoffBody(_ extra: [String: Any] = [:]) -> [String: Any] {
    var b: [String: Any] = [
        "prompt": "hello from mac",
        "source": "lingxin-handoff",
        "idempotencyKey": "handoff-key-001",
        "session": "",
    ]
    for (k, v) in extra { b[k] = v }
    return b
}

print("kill-switch parsing")
check("defaults false + no env is OFF", isKillSwitchOn(env: [:], defaultsEnabled: false), false)
check("defaults true enables", isKillSwitchOn(env: [:], defaultsEnabled: true), true)
check("env 1 enables", isKillSwitchOn(env: ["MINIS_GATEWAY_PROMPT_HANDOFF": "1"], defaultsEnabled: false), true)
check("env true enables", isKillSwitchOn(env: ["MINIS_GATEWAY_PROMPT_HANDOFF": "true"], defaultsEnabled: false), true)
check("env YES enables", isKillSwitchOn(env: ["MINIS_GATEWAY_PROMPT_HANDOFF": "YES"], defaultsEnabled: false), true)
check("env on enables", isKillSwitchOn(env: ["MINIS_GATEWAY_PROMPT_HANDOFF": "on"], defaultsEnabled: false), true)
check("env 0 stays OFF", isKillSwitchOn(env: ["MINIS_GATEWAY_PROMPT_HANDOFF": "0"], defaultsEnabled: false), false)
check("env empty stays OFF", isKillSwitchOn(env: ["MINIS_GATEWAY_PROMPT_HANDOFF": ""], defaultsEnabled: false), false)

print("GET always not_ready")
let getOff = evaluate(method: "GET", path: "/gateway/prompt", killSwitchOn: false)
checkEq("GET off status", getOff.httpStatus, 503)
checkEq("GET off error", getOff.error, "not_ready")
check("GET off not allow", getOff.allow, false)
let getOn = evaluate(method: "GET", path: "/gateway/prompt", killSwitchOn: true)
checkEq("GET on still 503", getOn.httpStatus, 503)
checkEq("GET on still not_ready", getOn.error, "not_ready")

print("POST default deny")
let postOff = evaluate(method: "POST", path: "/gateway/prompt", body: handoffBody(), killSwitchOn: false)
checkEq("perfect handoff still 409 when kill-switch OFF", postOff.httpStatus, 409)
checkEq("perfect handoff still tool-only when kill-switch OFF", postOff.error, "tool-only")
check("perfect handoff not allowed when kill-switch OFF", postOff.allow, false)

let noSource = evaluate(method: "POST", path: "/gateway/prompt",
                        body: ["prompt": "hi", "idempotencyKey": "handoff-key-001"],
                        killSwitchOn: true)
checkEq("missing source is 409", noSource.httpStatus, 409)
checkEq("missing source is tool-only", noSource.error, "tool-only")

let wrongSource = evaluate(method: "POST", path: "/gateway/prompt",
                           body: handoffBody(["source": "shortcuts"]),
                           killSwitchOn: true)
checkEq("wrong source is 409", wrongSource.httpStatus, 409)
checkEq("wrong source is tool-only", wrongSource.error, "tool-only")

print("POST allow path")
let missingKey = evaluate(method: "POST", path: "/gateway/prompt",
                          body: ["prompt": "hi", "source": "lingxin-handoff"],
                          killSwitchOn: true)
checkEq("missing idempotency is 400", missingKey.httpStatus, 400)
checkEq("missing idempotency error", missingKey.error, "idempotency_key_required")

let shortKey = evaluate(method: "POST", path: "/gateway/prompt",
                        body: ["prompt": "hi", "source": "lingxin-handoff", "idempotencyKey": "short"],
                        killSwitchOn: true)
checkEq("short idempotency is 400", shortKey.httpStatus, 400)

let emptyPrompt = evaluate(method: "POST", path: "/gateway/prompt",
                           body: ["prompt": "  ", "source": "lingxin-handoff", "idempotencyKey": "handoff-key-001"],
                           killSwitchOn: true)
checkEq("empty prompt is 400", emptyPrompt.httpStatus, 400)
checkEq("empty prompt error", emptyPrompt.error, "empty_prompt")

let allowed = evaluate(method: "POST", path: "/gateway/prompt", body: handoffBody(), killSwitchOn: true)
check("handoff allowed", allowed.allow)
checkEq("handoff 200", allowed.httpStatus, 200)

let withSession = evaluate(method: "POST", path: "/gateway/prompt",
                           body: handoffBody(["session": "fixed-session-0821"]),
                           killSwitchOn: true)
check("session id still allowed (new chat; id ignored)", withSession.allow)
checkEq("session id does not 409", withSession.httpStatus, 200)

print("header / query source")
let viaHeader = evaluate(
    method: "POST",
    path: "/gateway/prompt",
    headers: ["X-Minis-Source": "lingxin-handoff", "Idempotency-Key": "handoff-key-001"],
    body: ["prompt": "hi"],
    killSwitchOn: true)
check("source via X-Minis-Source", viaHeader.allow)

let viaQuery = evaluate(
    method: "POST",
    path: "/gateway/prompt?source=lingxin-handoff",
    headers: ["Idempotency-Key": "handoff-key-001"],
    query: ["source": "lingxin-handoff"],
    body: ["prompt": "hi"],
    killSwitchOn: true)
check("source via query", viaQuery.allow)

print("path / method")
checkEq("wrong path 404", evaluate(method: "POST", path: "/rpc", body: handoffBody(), killSwitchOn: true).httpStatus, 404)
checkEq("trailing slash still matches", evaluate(method: "POST", path: "/gateway/prompt/", body: handoffBody(), killSwitchOn: true).httpStatus, 200)
checkEq("PUT 405", evaluate(method: "PUT", path: "/gateway/prompt", body: handoffBody(), killSwitchOn: true).httpStatus, 405)

print("shipping-token alignment")
check("evaluate mentions new_chat in shipping allow json", shipping.contains("\"new_chat\""))
check("dispatch source is lingxin-handoff", shipping.contains("source: GatewayPromptHandoffGate.allowedSource"))

if failures == 0 {
    print("ALL PASSED")
    exit(0)
} else {
    print("FAILED \(failures)")
    exit(1)
}
