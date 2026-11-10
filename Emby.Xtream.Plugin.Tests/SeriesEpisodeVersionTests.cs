using System.IO;
using System.Threading.Tasks;
using Emby.Xtream.Plugin.Service;
using Xunit;

namespace Emby.Xtream.Plugin.Tests
{
    /// <summary>
    /// Episodes Dispatcharr has from more than one provider stream get one .strm per stream,
    /// named and addressed as movie versions are (ADR-025).
    /// </summary>
    public class SeriesEpisodeVersionTests : SyncTestBase
    {
        private string EpisodeDir => Path.Combine(TempDir.Path, "Shows", "Test Show", "Season 01");
        private string Plain => Path.Combine(EpisodeDir, "Test Show - S01E01 - Episode Title.strm");
        private string Second => Path.Combine(EpisodeDir, "Test Show - S01E01 - Episode Title - Version 2.strm");

        private PluginConfiguration DispatcharrConfig()
        {
            var config = DefaultConfig();
            config.EnableDispatcharr = true;
            config.DispatcharrUrl = "http://fake-dispatcharr";
            config.DispatcharrUser = "admin";
            config.DispatcharrPass = "secret";
            return config;
        }

        private StrmSyncService ServiceWithSeries()
        {
            Handler.RespondWith("action=get_series_info&series_id=1", SeriesDetailJson());
            Handler.RespondWith("action=get_series", SeriesListJson(Series(seriesId: 1, name: "Test Show", lastModified: "2000")));
            Handler.RespondWith("/api/accounts/token/", "{\"access\":\"tok\",\"refresh\":\"ref\"}");
            var svc = MakeService();
            svc.DispatcharrHandler = Handler;
            return svc;
        }

        [Fact]
        public async Task EpisodeWithTwoProviders_WritesEveryVersion()
        {
            var svc = ServiceWithSeries();
            Handler.RespondWith("api/vod/series/1/providers/", "[{\"id\":1},{\"id\":2}]");
            Handler.RespondWith("api/vod/series/1/episodes/",
                "[{\"id\":101,\"uuid\":\"ep-uuid\",\"providers\":[{\"id\":1,\"stream_id\":\"501\"},{\"id\":2,\"stream_id\":502}]}]");

            await svc.SyncSeriesAsync(DispatcharrConfig(), None, SaveConfig);

            Assert.Equal("http://fake-dispatcharr/proxy/vod/episode/ep-uuid?stream_id=501", File.ReadAllText(Plain));
            Assert.Equal("http://fake-dispatcharr/proxy/vod/episode/ep-uuid?stream_id=502", File.ReadAllText(Second));
        }

        [Fact]
        public async Task EpisodeWithOneProvider_KeepsThePlainXtreamFile()
        {
            var svc = ServiceWithSeries();
            Handler.RespondWith("api/vod/series/1/providers/", "[{\"id\":1},{\"id\":2}]");
            Handler.RespondWith("api/vod/series/1/episodes/",
                "[{\"id\":101,\"uuid\":\"ep-uuid\",\"providers\":[{\"id\":1,\"stream_id\":\"501\"}]}]");

            await svc.SyncSeriesAsync(DispatcharrConfig(), None, SaveConfig);

            Assert.Equal("http://fake-xtream/series/user/pass/101.mp4", File.ReadAllText(Plain));
            Assert.False(File.Exists(Second));
        }

        [Fact]
        public async Task DispatcharrLookupFails_WritesThePlainFile()
        {
            var svc = ServiceWithSeries();
            Handler.RespondWith("api/vod/series/1/providers/", "[{\"id\":1},{\"id\":2}]");
            Handler.RespondWith("api/vod/series/1/episodes/", "not json");

            await svc.SyncSeriesAsync(DispatcharrConfig(), None, SaveConfig);

            Assert.Equal("http://fake-xtream/series/user/pass/101.mp4", File.ReadAllText(Plain));
            Assert.False(File.Exists(Second));
        }

        [Fact]
        public async Task SeriesWithOneProvider_NeverAsksForTheEpisodeList()
        {
            var svc = ServiceWithSeries();
            Handler.RespondWith("api/vod/series/1/providers/", "[{\"id\":1}]");

            await svc.SyncSeriesAsync(DispatcharrConfig(), None, SaveConfig);

            Assert.DoesNotContain(Handler.ReceivedUrls, u => u.Contains("/episodes/"));
            Assert.Equal("http://fake-xtream/series/user/pass/101.mp4", File.ReadAllText(Plain));
            Assert.False(File.Exists(Second));
        }

        [Fact]
        public void EpisodeHash_WithoutVersions_IsUnchanged()
        {
            var episodes = new System.Collections.Generic.Dictionary<string, System.Collections.Generic.List<Emby.Xtream.Plugin.Client.Models.EpisodeInfo>>
            {
                ["1"] = new System.Collections.Generic.List<Emby.Xtream.Plugin.Client.Models.EpisodeInfo>
                {
                    new Emby.Xtream.Plugin.Client.Models.EpisodeInfo { Id = 101, ContainerExtension = "mp4" }
                }
            };
            var versions = new System.Collections.Generic.Dictionary<int, StrmSyncService.EpisodeVersions>
            {
                [101] = new StrmSyncService.EpisodeVersions { Uuid = "u", StreamIds = new System.Collections.Generic.List<string> { "1", "2" } }
            };

            Assert.Equal(StrmSyncService.ComputeSeriesEpisodeHash(episodes),
                StrmSyncService.ComputeSeriesEpisodeHash(episodes, new System.Collections.Generic.Dictionary<int, StrmSyncService.EpisodeVersions>()));
            Assert.NotEqual(StrmSyncService.ComputeSeriesEpisodeHash(episodes),
                StrmSyncService.ComputeSeriesEpisodeHash(episodes, versions));
        }
    }
}
