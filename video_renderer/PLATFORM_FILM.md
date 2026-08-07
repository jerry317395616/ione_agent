# I-ONE AI Platform Film

This Remotion composition produces the long-form I-ONE AI platform film from real, aggregate-only product screens.

## Deliverable

- Composition: `PlatformFilm`
- Resolution: 1920 x 1080
- Frame rate: 30 fps
- Runtime: 187 seconds
- Chapters: 17
- Audio: Chinese neural narration, original ambient music bed, AAC loudness-normalized to -16 LUFS
- Captions: burned-in Chinese captions plus a standalone SRT file

## Rebuild

```powershell
$env:MANAGER_USER = "Administrator"
$env:MANAGER_PASSWORD = "..."
npm run capture:platform
npm run audio:platform
npm run manifest:platform
npm run qa:platform
npm run render:platform
```

The capture script only targets dashboards, workspaces, and list overviews. Do not add routes containing patient, employee, or customer detail records.

Generated audio and final output are intentionally excluded from Git. Source screens and the editorial manifest are versioned so visual and narrative changes are reviewable.

Before commercial distribution, confirm that the organization's Remotion usage complies with the current Remotion license.
