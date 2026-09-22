package com.openminis.app.service

/**
 * [T-android-swipe-keepalive-autonomous] Pure keep-alive decision used by
 * [AgentForegroundService.onTaskRemoved].
 *
 * Swipe-from-recents clears Activity presence. The foreground service must
 * still survive when:
 *  - a stream is already registered in [activeSessionIds], OR
 *  - a headless / scheduled run has [armedSwipeKeepAliveIds] for the gap
 *    between "FGS started + session resolved" and ChatViewModel.setActive
 *    (which only runs after acquireSlot inside streamJob).
 *
 * Presence alone must NOT keep the service alive after an explicit Recents
 * dismiss — that remains T166 behaviour.
 */
object TaskRemovalKeepAlivePolicy {
    fun shouldSurviveTaskRemoval(
        activeSessionIds: Collection<String>,
        armedSwipeKeepAliveIds: Collection<String>,
    ): Boolean = activeSessionIds.isNotEmpty() || armedSwipeKeepAliveIds.isNotEmpty()
}
