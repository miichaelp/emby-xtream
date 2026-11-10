using System;
using System.IO;
using System.Threading.Tasks;
using Emby.Xtream.Plugin.Service;
using Xunit;

namespace Emby.Xtream.Plugin.Tests
{
    /// <summary>
    /// The Stop button: a stopped sync keeps what it wrote, starts nothing new, and never reaches
    /// orphan cleanup or the watermark, since what it saw is not the whole catalogue (ADR-013).
    /// </summary>
    public class StopSyncTests : SyncTestBase
    {
        [Fact]
        public void Stop_WithNothingRunning_ReturnsFalse()
        {
            var svc = MakeService();
            Assert.False(svc.StopMovieSync());
            Assert.False(svc.StopSeriesSync());
        }

        [Fact]
        public async Task StoppedSeriesSync_KeepsFilesAndSkipsCleanupAndWatermark()
        {
            var config = DefaultConfig();
            config.CleanupOrphans = true;

            // A file an earlier sync wrote for a show the provider no longer lists. A completed
            // run would delete it; a stopped one must not.
            var orphan = Path.Combine(TempDir.Path, "Shows", "Old Show", "Season 01", "Old Show - S01E01.strm");
            Directory.CreateDirectory(Path.GetDirectoryName(orphan));
            File.WriteAllText(orphan, "http://fake-xtream/series/user/pass/999.mp4");

            Handler.RespondWith("action=get_series_info&series_id=1", SeriesDetailJson(seriesId: 1));
            Handler.RespondWith("action=get_series_info&series_id=2", SeriesDetailJson(seriesId: 2));
            Handler.RespondWith("action=get_series_info&series_id=3", SeriesDetailJson(seriesId: 3));
            Handler.RespondWith("action=get_series", SeriesListJson(
                Series(seriesId: 1, name: "Show One", lastModified: "2000"),
                Series(seriesId: 2, name: "Show Two", lastModified: "2001"),
                Series(seriesId: 3, name: "Show Three", lastModified: "2002")));

            // Hold series 2 in flight; series 3 then waits for the single worker slot.
            var hold = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            Handler.UrlGates["series_id=2"] = hold;

            var svc = MakeService();
            var run = svc.SyncSeriesAsync(config, None, SaveConfig);

            var deadline = DateTime.UtcNow.AddSeconds(10);
            while (!File.Exists(Path.Combine(TempDir.Path, "Shows", "Show One", "Season 01", "Show One - S01E01 - Episode Title.strm")))
            {
                Assert.True(DateTime.UtcNow < deadline, "series 1 was never written");
                await Task.Delay(10);
            }

            Assert.True(svc.StopSeriesSync());
            hold.SetResult(true);

            await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);

            Assert.True(File.Exists(orphan), "A stopped sync must not run orphan cleanup");
            Assert.False(Directory.Exists(Path.Combine(TempDir.Path, "Shows", "Show Three")),
                "No series may be started after Stop");
            Assert.Equal(0, config.LastSeriesSyncTimestamp);
            Assert.DoesNotContain(svc.FailedItems, i => i.StreamId == 3);
            Assert.Equal("Stopped", svc.SeriesProgress.Phase);
            Assert.False(svc.SeriesProgress.IsRunning);
            Assert.Equal(StrmSyncService.StoppedReason, svc.SeriesProgress.AbortReason);
            Assert.False(svc.StopSeriesSync(), "Nothing is running any more");
        }

        [Fact]
        public async Task StoppedMovieSync_SkipsCleanupAndWatermark()
        {
            var config = DefaultConfig();
            config.CleanupOrphans = true;

            var orphan = Path.Combine(TempDir.Path, "Movies", "Old Movie", "Old Movie.strm");
            Directory.CreateDirectory(Path.GetDirectoryName(orphan));
            File.WriteAllText(orphan, "http://fake-xtream/movie/user/pass/999.mkv");

            var hold = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            Handler.UrlGates["get_vod_streams"] = hold;
            Handler.RespondWith("get_vod_streams", VodStreamsJson(
                VodStream(streamId: 1, name: "Movie One", added: 1000),
                VodStream(streamId: 2, name: "Movie Two", added: 1001)));

            var svc = MakeService();
            var run = svc.SyncMoviesAsync(config, None, SaveConfig);
            Assert.True(svc.StopMovieSync());
            hold.SetResult(true);

            await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);

            Assert.True(File.Exists(orphan), "A stopped sync must not run orphan cleanup");
            Assert.Equal(0, config.LastMovieSyncTimestamp);
            Assert.Equal("Stopped", svc.MovieProgress.Phase);
        }
    }
}
