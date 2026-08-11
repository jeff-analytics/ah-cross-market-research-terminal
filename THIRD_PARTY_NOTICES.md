# Third-party notices

This project is an independent implementation.

Public references used for product research include:

- Koyfin public product/help pages for general watchlist, dashboard, screener, saved-view and navigation interaction patterns.
- `xcnecon/A-H-Premium-Arbitrage-Monitor` for public A/H monitoring workflow comparison and hybrid-source design ideas.

No Koyfin trademark, proprietary source code, private API, branded artwork, icon assets or copied interface files are included. The UI is independently implemented for the A/H research use case.

The bundled fallback A/H registry and demo history are used only for offline bootstrap. They are explicitly labelled as local cache and are not used as a trusted live intraday baseline.


## ECB Data Portal

The terminal may use the European Central Bank Data Portal's public SDMX web service as a fallback source for **historical daily** FX conversion. It requests the official daily CNY/EUR and HKD/EUR reference-rate series and derives CNY/HKD by cross-rate. This fallback does not replace the intraday/live FX provider stack.
