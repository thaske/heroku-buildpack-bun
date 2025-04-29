# heroku-buildpack-bun

Heroku buildpack for [Bun.js](https://bun.sh/) - allows you to run Bun on Heroku with efficient caching and proper security.

Largely inspired by the [Deno buildpack](https://github.com/chibat/heroku-buildpack-deno) and [Node.js buildpack](https://github.com/heroku/heroku-buildpack-nodejs).

## How to use

To add the buildpack to your Heroku app, visit the settings page for your app on Heroku, then under 'Buildpacks' add the URL `https://github.com/jakeg/heroku-buildpack-bun`.

You'll either need a [`Procfile`](https://devcenter.heroku.com/articles/procfile) in the root folder of your app (with eg `web: bun index.js` in it), or a `package.json` with a start script listed.

## Version pinning

Pin a certain Bun version such as `v1.1.20` with the `BUN_VERSION` environment variable (eg under 'Config Vars' on your app's Heroku settings page), or with a `.bun-version`, `runtime.bun.txt` or `runtime.txt` containing a single line for the pinned version. The version can be specified with or without a leading `v` eg `v1.0.7` or `1.0.7` or [any other Bun tags](https://github.com/oven-sh/bun/tags).

## Automatic detection

This buildpack automatically detects Bun applications by checking for:

- A `package.json` file with either an `engines.bun` field or a `.bun` field

This allows it to work properly in multi-buildpack environments.

## Support scripts

This buildpack automatically runs the following bun commands and scripts if defined in `package.json`:

- install (`bun install --production --frozen-lockfile`)
- heroku-prebuild (`bun run heroku-prebuild`)
- build (`bun run build`)
- heroku-postbuild (`bun run heroku-postbuild`)

Optionally skip any of these steps with files named `.skip-bun-install`, `.skip-bun-heroku-prebuild`, `.skip-bun-build` or `.skip-bun-heroku-postbuild`.

## Performance optimizations

This buildpack includes several optimizations:

- Caches Bun installation between deploys
- Uses `--production --frozen-lockfile` flags for reproducible and smaller installs
- Prunes development dependencies to reduce slug size
- Properly handles error conditions to prevent broken deploys

## Binding to correct port

Bind to `env.PORT` eg

```js
import { env } from "process";

const server = Bun.serve({
  port: env.PORT || 3000,
  fetch(request) {
    return new Response(`Welcome to Bun running on Heroku!`);
  },
});

console.log(`Listening on localhost:${server.port}`);
```

## Environment variables

The buildpack sets the following environment variables:

- `PATH`: Includes Bun's bin directory
- `BUN_DIR`: Points to Bun's cache directory

## Troubleshooting

If you're experiencing issues with the buildpack:

1. Check that your `package.json` includes Bun in the engines field: `"engines": { "bun": "1.x" }`
2. Enable verbose logging with `BUN_INSTALL_VERBOSE=1`
3. For performance profiling, use `BUN_PROFILE=1`

Use the Issues tab to report any bugs or request features.
