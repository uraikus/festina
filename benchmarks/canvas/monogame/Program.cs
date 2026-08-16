// claude.md #105: the MonoGame half of the canvas benchmark.
//
// The same frame as draw_shapes.f and draw_shapes.js -- 20,000 filled
// rectangles and 20,000 filled circles at identical coordinates, with
// the colour changed between every shape -- drawn through SpriteBatch
// into an offscreen RenderTarget2D.
//
// Written the way MonoGame is meant to be written, not as a strawman.
// A filled rectangle is a 1x1 white texture stretched and tinted, a
// circle is a pre-rendered circle texture tinted the same way, and both
// go through a single deferred SpriteBatch so the framework batches
// them into a couple of draw calls. That batching is the whole point of
// the design and it would be dishonest to defeat it.
//
// Worth noticing: a MonoGame circle is a pre-rendered texture stamped
// per instance, which is exactly what claude.md #104 made Festina's
// drawCircle do internally. The two converged on the same trick from
// opposite directions.
//
// The GetData call after End() is load-bearing, for the same reason the
// browser side reads a pixel back: GL is asynchronous, and timing the
// submission alone would measure how fast MonoGame can fill a command
// buffer rather than how fast anything is drawn.
//
// It reads the WHOLE target rather than one pixel, and that detail was
// measured rather than assumed. Three sync strategies on this workload:
//
//     no readback     min 516  median 526  max 538 ms
//     one pixel       min 193  median 519  max 553 ms
//     whole target    min 188  median 195  max 272 ms
//
// A one-pixel read syncs only sometimes, which is why its numbers swing
// by 3x within a single run; no readback at all is WORSE than either,
// because frames queue up and the timed region ends up containing some
// other frame's backlog. Reading the whole target forces a real finish,
// so each timed region holds exactly one frame -- and it costs 0.4 ms
// on an untouched target, measured, so it is not what is being timed.
using System;
using System.Diagnostics;
using System.IO;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;

namespace FestinaBench
{
    public class DrawShapes : Game
    {
        private const int Shapes = 20000;
        private const int Width = 800;
        private const int Height = 600;
        private const int Warmups = 3;
        private const int Runs = 15;

        private readonly GraphicsDeviceManager _gdm;
        private readonly string _pngPath;

        public bool StartupOnly { get; set; }

        public DrawShapes(string pngPath)
        {
            _pngPath = pngPath;
            _gdm = new GraphicsDeviceManager(this);
            _gdm.PreferredBackBufferWidth = Width;
            _gdm.PreferredBackBufferHeight = Height;
            _gdm.SynchronizeWithVerticalRetrace = false;
            IsFixedTimeStep = false;
        }

        /// A filled circle of the given radius, rasterized once into a
        /// texture with the same coverage-based antialiasing Cairo
        /// applies -- so the two sides' circles are the same picture and
        /// not merely the same size.
        private Texture2D MakeCircle(int radius)
        {
            int size = radius * 2 + 2;
            var data = new Color[size * size];
            double centre = size / 2.0;
            for (int y = 0; y < size; y++)
            {
                for (int x = 0; x < size; x++)
                {
                    // 4x4 supersampled coverage of the disc.
                    int hits = 0;
                    for (int sy = 0; sy < 4; sy++)
                    {
                        for (int sx = 0; sx < 4; sx++)
                        {
                            double px = x + (sx + 0.5) / 4.0 - centre;
                            double py = y + (sy + 0.5) / 4.0 - centre;
                            if (px * px + py * py <= radius * radius) hits++;
                        }
                    }
                    byte a = (byte)(hits * 255 / 16);
                    data[y * size + x] = new Color(a, a, a, a);
                }
            }
            var tex = new Texture2D(GraphicsDevice, size, size);
            tex.SetData(data);
            return tex;
        }

        protected override void LoadContent()
        {
            var target = new RenderTarget2D(GraphicsDevice, Width, Height);
            var batch = new SpriteBatch(GraphicsDevice);

            var pixel = new Texture2D(GraphicsDevice, 1, 1);
            pixel.SetData(new[] { Color.White });
            var circle = MakeCircle(4);
            int circleSize = circle.Width;

            var readback = new Color[Width * Height];
            if (StartupOnly)
            {
                Console.WriteLine("RESULT startup-only");
                Environment.Exit(0);
            }

            double best = double.MaxValue;
            double[] samples = new double[Runs];

            for (int run = 0; run < Warmups + Runs; run++)
            {
                GraphicsDevice.SetRenderTarget(target);
                GraphicsDevice.Clear(Color.White);

                var sw = Stopwatch.StartNew();
                batch.Begin(SpriteSortMode.Deferred, BlendState.AlphaBlend);
                for (int i = 0; i < Shapes; i++)
                {
                    int x = (i * 37) % 780;
                    int y = (i * 53) % 580;
                    var tint = new Color(i % 255, (i * 7) % 255, (i * 13) % 255);
                    batch.Draw(pixel, new Rectangle(x, y, 12, 9), tint);
                    batch.Draw(circle,
                                new Vector2(x + 6 - circleSize / 2.0f, y + 4 - circleSize / 2.0f),
                                tint);
                }
                batch.End();
                // Forces the GL pipeline to actually finish -- see this
                // file's header comment for why this reads everything.
                target.GetData(readback);
                sw.Stop();

                if (run >= Warmups)
                {
                    double ms = sw.Elapsed.TotalMilliseconds;
                    samples[run - Warmups] = ms;
                    if (ms < best) best = ms;
                }
            }

            Array.Sort(samples);
            double median = samples[samples.Length / 2];

            using (var stream = File.Create(_pngPath))
            {
                target.SaveAsPng(stream, Width, Height);
            }

            Console.WriteLine($"RESULT min={best:F3} median={median:F3}");
            Environment.Exit(0);
        }

        public static void Main(string[] args)
        {
            // "--startup-only" builds the device, the render target and
            // the textures, then exits without drawing a frame. Timing
            // that whole process is how the runner measures "getting to
            // the first frame" -- measured directly rather than by
            // subtracting frames from the full run, which produced a
            // negative (clamped to zero) answer the moment a contended
            // run inflated the frame time.
            string png = "monogame_canvas.png";
            bool startupOnly = false;
            foreach (var a in args)
            {
                if (a == "--startup-only") startupOnly = true;
                else png = a;
            }
            using (var game = new DrawShapes(png) { StartupOnly = startupOnly })
            {
                game.Run();
            }
        }
    }
}
