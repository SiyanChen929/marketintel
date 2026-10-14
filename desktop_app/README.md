# Market Intel desktop app

The macOS app is a local WebKit shell for `http://127.0.0.1:8787/`.

- Project root: `$MARKET_INTEL_HOME` if set (e.g. `launchctl setenv MARKET_INTEL_HOME /path/to/marketintel`), otherwise the checkout containing `dist/`
- Rebuild: `./scripts/build_market_intel_app.sh`
- Built copy: `dist/Market Intel.app`
- Service log: `outputs/logs/market_intel.out`
- Standalone fallback log: `outputs/logs/market_intel_desktop.out`

On launch, the app checks `/api/status`. If the login service is unavailable, it
tries to restart `com.siyanchen.marketintel`; if that job is not installed, it starts
the Python service directly from this project. Only the local dashboard is shown
inside the app. External links open in the default browser.
