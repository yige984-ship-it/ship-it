# Tiangong phase-one status

Base: feature/tiangong-ai-studio-v1, 30f67971881e95df4e4181ba24b79d848084db28 (draft PR #2).

Workbench: /tiangong. Supports one pilot, 18 fixed five-second shot records, character lock fields, reference uploads and bindings, and shot editing. Character edits invalidate dependent shot approval/provenance and captured versions. References are user uploads, not verified identity matches. No generation endpoint or paid API call was added. The workbench does not produce real AI video.

Persistence: manifest and asset metadata in DATA_DIR/studio.sqlite3; asset bytes in DATA_DIR/tiangong_assets. Verified reads after creating a fresh application instance. Current upload limit is 60,000 bytes and files are downloaded as attachments. Larger reference images and video ingestion remain pending.

Render's checked-in free configuration sets EPHEMERAL_STORAGE=true. This is not durable hosting: a restart/redeploy may remove data. The workbench displays this condition. Production persistence requires a persistent volume or object storage; no hosting change or deployment performed.

Verification: Python 3.12.16; requirements installed; pip check passed; pytest: 59 passed, one Starlette/AnyIO deprecation warning; compileall and git diff --check passed. New integration coverage checks reference upload, invalid encoding, missing references, whitespace rejection, bindings, character version capture/invalidation and reads across app recreation. Local server starts. Browser tool timed out; visual and interactive browser acceptance remains pending.

Budget: first round cap CNY 140, paid calls disabled; no paid calls made. Real generation, provenance verification, cinematic quality review and final video export remain incomplete. Existing original studio mock renderer remains explicitly simulated.

## Reference image upload follow-up

Added POST /api/tiangong/pilot/assets/image using raw image bytes, up to 10 MiB; PNG/JPEG/WebP only, validated with Pillow, maximum 25 million pixels. Workbench uses this endpoint. Global request limit remains unchanged for other endpoints. Image assets are capped at 100 through this endpoint. Failed manifest saves clean up the newly written image file. Legacy small-file upload remains for compatibility.

New checks cover a >60 KB real PNG, persistence across app recreation, invalid image rejection and >10 MiB rejection. Browser interaction acceptance remains pending; no real AI generation performed.

## Integrity, backup and browser acceptance follow-up

Added authenticated readiness and ZIP backup endpoints. Backup contains manifest.json and referenced stored assets; missing files or SHA-256 mismatches block export. Readiness lists empty shot prompts, missing character references, stale character versions and corrupt/missing stored assets. It explicitly reports real_video_ready=false. Character edits now clear stale reference snapshots as well as captured versions. Both upload paths enforce the 100-asset cap.

Browser acceptance used an isolated temporary DATA_DIR and headless installed Chrome: initialize character, see 18 shots, upload >60 KB PNG, bind reference, save shot, reload and retain edits, show readiness blockers and download backup. No JavaScript errors. A 390px viewport overflow was found and fixed; the complete flow and mobile width check then passed. No production or user browser profile data was used. The earlier browser acceptance limitation is resolved for these tested flows.

Authentication coverage explicitly checks pilot, storage, readiness, backup, asset download and image upload reject unauthenticated requests. Backups are downloadable archives; an automated restore flow is not yet implemented. Durable hosted storage remains pending; no Render changes or deployment were made.
