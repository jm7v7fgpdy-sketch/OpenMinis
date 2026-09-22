package com.openminis.app.service

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * [T-android-swipe-keepalive-autonomous] Swipe-from-recents must keep the
 * foreground service alive while a headless / scheduled agent run is armed
 * OR a stream is already active — otherwise autonomous messages die the
 * moment the user clears the app from Recents.
 */
class TaskRemovalKeepAlivePolicyTest {

    @Test
    fun `empty active and empty arm stops the service`() {
        assertFalse(
            TaskRemovalKeepAlivePolicy.shouldSurviveTaskRemoval(
                activeSessionIds = emptySet(),
                armedSwipeKeepAliveIds = emptySet(),
            ),
        )
    }

    @Test
    fun `active stream alone survives swipe`() {
        assertTrue(
            TaskRemovalKeepAlivePolicy.shouldSurviveTaskRemoval(
                activeSessionIds = setOf("sess-1"),
                armedSwipeKeepAliveIds = emptySet(),
            ),
        )
    }

    @Test
    fun `armed headless run alone survives swipe before setActive`() {
        // The scheduled / headless path starts the FGS and resolves a session
        // id before ChatViewModel.streamJob reaches setActive. That gap is
        // exactly when a Recents swipe used to call stopSelf().
        assertTrue(
            TaskRemovalKeepAlivePolicy.shouldSurviveTaskRemoval(
                activeSessionIds = emptySet(),
                armedSwipeKeepAliveIds = setOf("sess-scheduled"),
            ),
        )
    }

    @Test
    fun `both active and armed survive swipe`() {
        assertTrue(
            TaskRemovalKeepAlivePolicy.shouldSurviveTaskRemoval(
                activeSessionIds = setOf("sess-1"),
                armedSwipeKeepAliveIds = setOf("sess-1"),
            ),
        )
    }
}
