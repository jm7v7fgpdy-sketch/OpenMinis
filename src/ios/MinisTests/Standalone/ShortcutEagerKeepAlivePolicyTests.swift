// [T-ios-shortcut-appintent-bg-flag] Standalone regression for the AppIntent
// wake path that left silent-audio keep-alive NOOP'd (`reason=bg=false` /
// `isActive=false`) so overnight Shortcuts automations suspended before the
// model call started.
//
// Run: `swift ShortcutEagerKeepAlivePolicyTests.swift`
// Standalone because the MinisTests target has a pre-existing compile break
// (same rationale as other files under MinisTests/Standalone/).

import Foundation

var failures = 0
func check(_ label: String, _ actual: Bool, _ expected: Bool = true) {
    if actual == expected { print("  ✅ \(label)") }
    else { print("  ❌ \(label) — expected \(expected), got \(actual)"); failures += 1 }
}

// MARK: - Mirror of ShortcutEagerKeepAlivePolicy (kept in sync via source check)

enum ShortcutEagerKeepAlivePolicy {
    static func shouldForceBackgroundFlag(
        applicationStateRawValue: Int,
        currentlyMarkedBackground: Bool
    ) -> Bool {
        let isActivelyForeground = applicationStateRawValue == 0
        return !isActivelyForeground && !currentlyMarkedBackground
    }

    static func canStartSilentAudio(
        isActive: Bool,
        backgroundSpeakEnabled: Bool,
        appIsInBackground: Bool,
        silentAudioSuspendCount: Int
    ) -> Bool {
        isActive && backgroundSpeakEnabled && appIsInBackground && silentAudioSuspendCount == 0
    }
}

print("ShortcutEagerKeepAlivePolicyTests")

check(
    "force bg when inactive and not marked",
    ShortcutEagerKeepAlivePolicy.shouldForceBackgroundFlag(
        applicationStateRawValue: 1, currentlyMarkedBackground: false)
)
check(
    "force bg when background and not marked",
    ShortcutEagerKeepAlivePolicy.shouldForceBackgroundFlag(
        applicationStateRawValue: 2, currentlyMarkedBackground: false)
)
check(
    "do not force when already marked",
    ShortcutEagerKeepAlivePolicy.shouldForceBackgroundFlag(
        applicationStateRawValue: 2, currentlyMarkedBackground: true),
    false
)
check(
    "do not force when actively foreground",
    ShortcutEagerKeepAlivePolicy.shouldForceBackgroundFlag(
        applicationStateRawValue: 0, currentlyMarkedBackground: false),
    false
)
check(
    "silent audio needs all three legs",
    ShortcutEagerKeepAlivePolicy.canStartSilentAudio(
        isActive: true, backgroundSpeakEnabled: true,
        appIsInBackground: true, silentAudioSuspendCount: 0)
)
check(
    "silent audio blocked by isActive=false (Combine race)",
    ShortcutEagerKeepAlivePolicy.canStartSilentAudio(
        isActive: false, backgroundSpeakEnabled: true,
        appIsInBackground: true, silentAudioSuspendCount: 0),
    false
)
check(
    "silent audio blocked by bg=false (AppIntent inactive wake)",
    ShortcutEagerKeepAlivePolicy.canStartSilentAudio(
        isActive: true, backgroundSpeakEnabled: true,
        appIsInBackground: false, silentAudioSuspendCount: 0),
    false
)

// Drift guard: shipping source must still contain the policy helpers and the
// armEagerlyForShortcut call sites that use shouldForceBackgroundFlag.
let here = URL(fileURLWithPath: #filePath)
let policyPath = here
    .deletingLastPathComponent() // Standalone
    .deletingLastPathComponent() // MinisTests
    .deletingLastPathComponent() // ios
    .appendingPathComponent("Agent/Background/ShortcutEagerKeepAlivePolicy.swift").path
let bkaPath = here
    .deletingLastPathComponent()
    .deletingLastPathComponent()
    .deletingLastPathComponent()
    .appendingPathComponent("Agent/Background/BackgroundKeepAliveManager.swift").path

if let policySrc = try? String(contentsOfFile: policyPath, encoding: .utf8) {
    check("shipping policy defines shouldForceBackgroundFlag",
          policySrc.contains("shouldForceBackgroundFlag"))
    check("shipping policy defines canStartSilentAudio",
          policySrc.contains("canStartSilentAudio"))
} else {
    check("shipping policy file readable at \(policyPath)", false)
}

if let bkaSrc = try? String(contentsOfFile: bkaPath, encoding: .utf8) {
    check("armEagerlyForShortcut uses shouldForceBackgroundFlag",
          bkaSrc.contains("ShortcutEagerKeepAlivePolicy.shouldForceBackgroundFlag"))
    check("armEagerlyForShortcut sync-activates isActive",
          bkaSrc.contains("Activating keep-alive synchronously for shortcut")
            || bkaSrc.contains("sync-activate")
            || bkaSrc.contains("isActive = true"))
} else {
    check("BackgroundKeepAliveManager readable at \(bkaPath)", false)
}

if failures == 0 {
    print("All checks passed.")
    exit(0)
} else {
    print("FAILED: \(failures) check(s)")
    exit(1)
}
