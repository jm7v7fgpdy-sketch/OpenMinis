import Foundation

#if canImport(UIKit)
import UIKit
#endif

/// [T-ios-shortcut-appintent-bg-flag] Pure helpers for Shortcuts / AppIntent
/// eager keep-alive arming. Extracted so the "force background" decision can
/// be unit-tested without spinning up AVAudioEngine.
enum ShortcutEagerKeepAlivePolicy {
    /// AppIntent wakes with `openAppWhenRun = false` typically land in
    /// `.inactive` (or already `.background`) — `didEnterBackground` never
    /// fires, so `appIsInBackground` stays false and silent-audio evaluate
    /// permanently NOOPs with `reason=bg=false`. Force the flag whenever the
    /// process is not actively foregrounded.
    static func shouldForceBackgroundFlag(
        applicationStateRawValue: Int,
        currentlyMarkedBackground: Bool
    ) -> Bool {
        // UIApplication.State: active=0, inactive=1, background=2
        let isActivelyForeground = applicationStateRawValue == 0
        return !isActivelyForeground && !currentlyMarkedBackground
    }

    /// Whether silent-audio evaluate can proceed for a shortcut arm once
    /// toggles are on. Used to document the hand-off: after forcing bg +
    /// syncing isActive, evaluate must see all three legs true.
    static func canStartSilentAudio(
        isActive: Bool,
        backgroundSpeakEnabled: Bool,
        appIsInBackground: Bool,
        silentAudioSuspendCount: Int
    ) -> Bool {
        isActive && backgroundSpeakEnabled && appIsInBackground && silentAudioSuspendCount == 0
    }
}
