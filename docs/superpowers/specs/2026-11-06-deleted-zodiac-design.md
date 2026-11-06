# Deleted Zodiac upload replacement

User-approved scope: replace the manually deleted date-brings-a-friend video using the current contrast-fixed renderer. Keep the original immutable draft, request, execution, content, music selection and future publication slot. Do not affect other episodes.

Approach: an explicit immutable authorization names one execution and one old YouTube ID. An authenticated runtime preparation workflow verifies the correct channel, validates the existing result and evidence, checks that the old video and content marker are absent, and archives the exact three old files before removing their active copies in one compare-and-swap git commit. The existing renderer/uploader then runs unchanged against that new source commit. A replay reuses the archive and existing duplicate fences; it never clears a new upload fence.

Alternatives rejected: blindly rerunning skips completed results; manually deleting fences loses auditability; inventing a new content ID changes the episode identity and slot.

Safety: API errors, a live old video, another marker match, expired slot, changed evidence, active execution, request mismatch, and push conflicts all stop before a new upload. Archive preparation writes only one archive plus removal of that episode's result, intent and upload files. Existing result ingestion explicitly validates this archive-only deletion commit. Normal new immutable-result ingestion remains unchanged.

The replacement and ordinary production jobs share the same per-execution concurrency lock. The execution passed for the lock must match the authorization. A bounded marker scan must finish pagination; otherwise replacement is forbidden, not assumed safe.
