using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace Emby.Xtream.Plugin.Client.Models
{
    public class DispatcharrVodMovieDetail
    {
        public int Id { get; set; }
        public string Uuid { get; set; }
    }

    public class DispatcharrVodProvider
    {
        public int Id { get; set; }

        [JsonPropertyName("stream_id")]
        public int StreamId { get; set; }

        public string Name { get; set; }
    }

    /// <summary>One provider relation of <c>/api/vod/series/{id}/providers/</c>. Only counted.</summary>
    public class DispatcharrVodSeriesProvider
    {
        public int Id { get; set; }
    }

    /// <summary>One episode of <c>/api/vod/series/{id}/episodes/</c>, with every provider stream that carries it.</summary>
    public class DispatcharrVodEpisode
    {
        /// <summary>Dispatcharr's episode ID: the <c>id</c> its Xtream <c>get_series_info</c> gives the episode.</summary>
        public int Id { get; set; }

        public string Uuid { get; set; }

        public List<DispatcharrVodEpisodeProvider> Providers { get; set; }
    }

    /// <summary>One provider relation of an episode. Dispatcharr stores <c>stream_id</c> as text.</summary>
    public class DispatcharrVodEpisodeProvider
    {
        public int Id { get; set; }

        [JsonPropertyName("stream_id")]
        [JsonConverter(typeof(TolerantStringConverter))]
        public string StreamId { get; set; }
    }
}
