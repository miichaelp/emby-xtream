import uuid

from django.db import models

from apps.m3u.models import M3UAccount


class VODCategory(models.Model):
    name = models.CharField(max_length=255)
    category_type = models.CharField(max_length=10, default="movie")

    class Meta:
        app_label = "vod"


class Movie(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, unique=True)
    name = models.CharField(max_length=255)
    year = models.IntegerField(null=True)
    tmdb_id = models.CharField(max_length=50, null=True)
    language = models.CharField(max_length=2, default="", blank=True)

    class Meta:
        app_label = "vod"


class Series(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, unique=True)
    name = models.CharField(max_length=255)
    year = models.IntegerField(null=True)
    tmdb_id = models.CharField(max_length=50, null=True)
    language = models.CharField(max_length=2, default="", blank=True)

    class Meta:
        app_label = "vod"


class Episode(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, unique=True)
    name = models.CharField(max_length=255)
    series = models.ForeignKey(Series, on_delete=models.CASCADE, related_name="episodes")
    season_number = models.IntegerField(null=True)
    episode_number = models.IntegerField(null=True)

    class Meta:
        app_label = "vod"


class M3UMovieRelation(models.Model):
    m3u_account = models.ForeignKey(M3UAccount, on_delete=models.CASCADE)
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name="m3u_relations")
    category = models.ForeignKey(VODCategory, on_delete=models.SET_NULL, null=True)
    stream_id = models.CharField(max_length=255)
    container_extension = models.CharField(max_length=10, null=True)

    class Meta:
        app_label = "vod"


class M3USeriesRelation(models.Model):
    m3u_account = models.ForeignKey(M3UAccount, on_delete=models.CASCADE)
    series = models.ForeignKey(Series, on_delete=models.CASCADE, related_name="m3u_relations")
    category = models.ForeignKey(VODCategory, on_delete=models.SET_NULL, null=True)
    external_series_id = models.CharField(max_length=255)
    custom_properties = models.JSONField(null=True)
    last_episode_refresh = models.DateTimeField(null=True)

    class Meta:
        app_label = "vod"


class M3UEpisodeRelation(models.Model):
    m3u_account = models.ForeignKey(M3UAccount, on_delete=models.CASCADE)
    episode = models.ForeignKey(Episode, on_delete=models.CASCADE, related_name="m3u_relations")
    stream_id = models.CharField(max_length=255)
    container_extension = models.CharField(max_length=10, null=True)

    class Meta:
        app_label = "vod"


class M3UVODCategoryRelation(models.Model):
    m3u_account = models.ForeignKey(M3UAccount, on_delete=models.CASCADE)
    category = models.ForeignKey(VODCategory, on_delete=models.CASCADE)
    enabled = models.BooleanField(default=False)

    class Meta:
        app_label = "vod"
