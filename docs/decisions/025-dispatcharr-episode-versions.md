# ADR-025: One .strm per Dispatcharr Provider Stream for Series Episodes

**Date**: 2026-11-10
**Status**: Accepted
**Affects**: `StrmSyncService.SyncSeriesCoreAsync()`, `StrmSyncService.FetchEpisodeVersionsAsync()`,
`StrmSyncService.BuildEpisodeStrmEntries()`, `StrmSyncService.ComputeSeriesEpisodeHash()`,
`DispatcharrClient.GetVodSeriesEpisodesAsync()`

---

## Context

With Dispatcharr enabled, a movie that Dispatcharr has from more than one provider stream gets one
`.strm` per stream: `{name}.strm` for the first and `{name} - Version N.strm` for the rest, each
pointing at `/proxy/vod/movie/{uuid}?stream_id={id}`. Emby then offers the versions in its version
picker. Series episodes always got a single Xtream `.strm`, so a user with the same show from two
providers only ever saw one.

## Problem

The episode IDs in Dispatcharr's Xtream `get_series_info` are Dispatcharr episode IDs, and its
Xtream `get_series_info` only exposes the highest-priority relation of each episode. The other
provider streams are only visible through Dispatcharr's own API.

## Alternatives Considered

### 1. One request per episode

`/api/vod/episodes/{id}/` does not list providers, and a request per episode would multiply the
sync's Dispatcharr traffic by the episode count.

### 2. `/api/vod/series/{id}/episodes/` (chosen)

One request per series returns every episode with its active provider relations (`providers[]`,
each with a `stream_id`). The series ID is the one Dispatcharr's Xtream `get_series` hands out,
and the episode `id` matches the one in `get_series_info`.

## Decision

- After the series detail is fetched (so unchanged series skipped by the watermark or smart skip
  cost no request), ask Dispatcharr for the series' episodes. Episodes with more than one distinct
  `stream_id` get the movie naming: the existing file name for the first version, ` - Version N`
  before `.strm` for the rest, each `{DispatcharrUrl}/proxy/vod/episode/{uuid}?stream_id={id}`.
- Episodes with one provider keep the plain Xtream URL, as movies do.
- Any lookup failure writes the plain files, as a failed movie lookup does.
- The episode hash includes the versions, so a stream added or removed in Dispatcharr rewrites the
  series. With no versions the hash is byte-for-byte the old one, so enabling this does not force a
  rewrite of every show.

## Consequences

- Version files start with `DispatcharrUrl`, so `StrmOwnership` already treats them as the
  plugin's, and orphan cleanup removes a `- Version N` file when Dispatcharr drops that stream.
- Shows already on disk whose series is skipped before the detail fetch (watermark + smart skip)
  only pick up versions when the provider changes them or after a full re-sync, the same as movies.
- One extra Dispatcharr request per processed series.
