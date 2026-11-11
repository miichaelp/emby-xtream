"""Stand-in for Dispatcharr's refresh_series_episodes: tests set `behaviour`."""
from django.utils import timezone

calls = []
behaviour = {}


def refresh_series_episodes(account, series, external_series_id, episodes_data=None):
    from apps.vod.models import M3USeriesRelation

    calls.append(external_series_id)
    outcome = behaviour.get(external_series_id, "ok")
    if outcome == "error":
        return  # Dispatcharr logs provider errors and returns without touching the relation
    relation = M3USeriesRelation.objects.get(m3u_account=account, external_series_id=external_series_id)
    props = dict(relation.custom_properties or {})
    props["episodes_fetched"] = outcome == "ok"
    relation.custom_properties = props
    relation.last_episode_refresh = timezone.now()
    relation.save()
