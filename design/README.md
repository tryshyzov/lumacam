# Design mockups

Interactive mockups of the two apps, kept as standalone pages so they can be
opened locally and imported into Figma.

- `lumalink-studio.html` — the Windows desktop app.
- `lumalink-companion.html` — the Android companion.

Open a file in Chrome. Add a state to the address after `#`, separated by
commas, to open the page already in that state:

| Page | States |
| --- | --- |
| `lumalink-studio.html` | `disconnected`, `paused`, `wifi`, `en`, `settings`, `profile`, `info`, `mirror` |
| `lumalink-companion.html` | `waiting`, `live`, `wifi`, `front`, `en`, `settings` |

For example `lumalink-studio.html#paused,en` or `lumalink-companion.html#live`.

## Import into Figma

1. Install the html.to.design plugin in Figma and its Chrome extension.
2. In `chrome://extensions`, open the extension's details and turn on
   **Allow access to file URLs**; otherwise it cannot read local files.
3. Open a mockup in Chrome in the state you want and start the extension.
4. Repeat for each state you need: each import captures the page as it is at
   that moment.

The canonical versions are the claude.ai artifacts; these copies are made
from them and hide the review notes.
