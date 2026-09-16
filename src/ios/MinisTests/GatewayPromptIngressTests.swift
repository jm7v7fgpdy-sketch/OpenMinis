import XCTest
@testable import Minis

/// XCTest mirror of the HANDOFF gate. The Standalone copy is the Linux-runnable
/// source of truth when this target cannot compile; keep the cases aligned.
final class GatewayPromptIngressTests: XCTestCase {

    private func decision(
        method: String,
        path: String = GatewayPromptHandoffGate.path,
        headers: [String: String] = [:],
        query: [String: String] = [:],
        body: [String: Any] = [:],
        kill: Bool
    ) -> GatewayPromptHandoffGate.Decision {
        GatewayPromptHandoffGate.evaluate(
            method: method, path: path, headers: headers,
            query: query, body: body, killSwitchOn: kill)
    }

    func testGetAlwaysNotReady() {
        for kill in [false, true] {
            let d = decision(method: "GET", kill: kill)
            XCTAssertFalse(d.allow)
            XCTAssertEqual(d.httpStatus, 503)
            XCTAssertEqual(d.json["status"] as? String, "not_ready")
        }
    }

    func testPostKillSwitchOffIsToolOnlyEvenWithHandoff() {
        let d = decision(
            method: "POST",
            headers: ["Idempotency-Key": "handoff-key-001"],
            body: ["prompt": "hi", "source": "lingxin-handoff"],
            kill: false)
        XCTAssertFalse(d.allow)
        XCTAssertEqual(d.httpStatus, 409)
        XCTAssertEqual(d.json["error"] as? String, "tool-only")
    }

    func testPostWrongSourceIsToolOnly() {
        let d = decision(
            method: "POST",
            body: ["prompt": "hi", "source": "cli", "idempotencyKey": "handoff-key-001"],
            kill: true)
        XCTAssertEqual(d.httpStatus, 409)
        XCTAssertEqual(d.json["error"] as? String, "tool-only")
    }

    func testAllowedHandoffRequiresIdempotencyAndPrompt() {
        XCTAssertEqual(
            decision(method: "POST",
                     body: ["prompt": "hi", "source": "lingxin-handoff"],
                     kill: true).httpStatus, 400)
        XCTAssertEqual(
            decision(method: "POST",
                     body: ["prompt": "", "source": "lingxin-handoff", "idempotencyKey": "handoff-key-001"],
                     kill: true).httpStatus, 400)
        let ok = decision(
            method: "POST",
            body: ["prompt": "hi", "source": "lingxin-handoff", "session": "ignored",
                   "idempotencyKey": "handoff-key-001"],
            kill: true)
        XCTAssertTrue(ok.allow)
        XCTAssertEqual(ok.httpStatus, 200)
        XCTAssertEqual(ok.json["new_chat"] as? Bool, true)
        XCTAssertNil(GatewayPromptHandoffGate.resolveSessionId(body: ["session": "ignored"]))
    }

    func testKillSwitchEnvParsing() {
        XCTAssertFalse(GatewayPromptHandoffGate.isKillSwitchOn(env: [:], defaultsEnabled: false))
        XCTAssertTrue(GatewayPromptHandoffGate.isKillSwitchOn(
            env: ["MINIS_GATEWAY_PROMPT_HANDOFF": "1"], defaultsEnabled: false))
        XCTAssertFalse(GatewayPromptHandoffGate.isKillSwitchOn(
            env: ["MINIS_GATEWAY_PROMPT_HANDOFF": "0"], defaultsEnabled: false))
        XCTAssertTrue(GatewayPromptHandoffGate.isKillSwitchOn(env: [:], defaultsEnabled: true))
    }
}
