# Tiangong AI Studio — Implementation Blueprint v0.1
Date: 2026-11-08
Status: architecture plan, not completed real video integration.

## Audit
Existing Xiuxian AI Studio uses FastAPI, SQLite, responsive HTML, FFmpeg, mock renders, image adapters, local editing, subtitles and offline voice. README says real video/voice adapters, per-shot regeneration, production QC and async queue remain unfinished. Preserve working functionality.

## Goal
Mac + cloud short-drama production. Pilot: 镇天执灯人, 18 shots x 5 seconds, 9:16. Reuse existing source and character assets; no invented canon.

## Architecture
- Additive /api/v2 FastAPI endpoints; existing routes remain functional.
- SQLite local MVP, PostgreSQL production, persistent object storage.
- Async jobs with Redis and Celery/RQ, idempotency, retry caps, cancellation and provider polling.
- Pluggable adapters for image, image-to-video, TTS, lipsync and QC. Never silently switch to paid models.
- FFmpeg for final assembly, subtitles, audio and export.

## Entities
Project(id,title,aspect_ratio,fps,budget_cny,status,revision)
Episode(id,project_id,script_version,target_seconds,status)
Character(id,project_id,reference_assets,identity,wardrobe,left_hand_ring,waist_token,shoes,version)
Shot(id,episode_id,order,duration_s,prompt,camera,action,character_ids,reference_assets,status)
GenerationJob(id,shot_id,provider,model,provider_job_id,input_hash,status,retries,reserved_cost,actual_cost)
Asset(id,shot_id,kind,uri,sha256,metadata,provenance,approved)
QualityReport(id,asset_id,checks,manual_decision,reasons)
CostLedger(id,job_id,reservation,charge,currency,timestamp)

## State machine
draft -> assets_ready -> queued -> generating -> generated -> qc_pending -> approved -> assembled
Rejected shots return to revision_pending. Mock-completed and real-completed are distinct states. Only approved assets enter final export.

## Acceptance
1. Import 18 five-second shots and maintain a versioned character bible.
2. Every real motion shot must have verifiable provider task and playable video, not FFmpeg zoom-pan.
3. Inspect face, left-hand ring, waist accessory, shoes, deformation, duplicates, black frames, watermark, subtitle, sound and aspect ratio.
4. Human approval for visual identity and accessories; automated checks alone cannot guarantee perfection.
5. Export 90-second 9:16 MP4 and validate with ffprobe and full visual review.
6. Enforce episode and shot budgets; require explicit approval before billable tasks.
7. Keep secrets out of Git; use authorization, HTTPS asset allowlists, persistent storage and logs.

## Milestones
M0: source audit, asset inventory, model capability and price verification.
M1: migrations, character bible, shot manifest, asset registry and UI.
M2: real video adapter and async queue; one-shot test only after billing confirmation.
M3: per-shot regeneration, QC, audio, 90-second export.
M4: multi-episode batches, dashboards, auth, monitoring and scalable workers.

## Immediate next tasks
Locate original chapters and character references. Add API v2 models and migration tests in separate PR. Keep main unchanged until tests and review. Never commit API keys.
