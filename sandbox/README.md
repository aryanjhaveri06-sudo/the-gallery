# sandbox/

Exploratory design studies from one session. **Not part of the app**, not
linked from anywhere, and not deployed — `index.html` is untouched.

Both were rejected by the owner. They are kept only so the work is
recoverable; delete the folder freely.

| File | What it is | Verdict |
| --- | --- | --- |
| `newsroom-hig.html` | The desk's own identity (paper, Playfair, gold) with the HIG accessibility fixes applied | Rejected |
| `newsroom-native.html` | Apple-native rebuild — SF, system colours, grouped inset lists, sidebar on iPad/Mac | Rejected |

Each carries real data: 40 lots, 17 events and 12 artists extracted from
`data/app_data.json`.

The one idea worth salvaging is the **estimate gauge**: it plots the hammer
price against the published estimate band, so above/below reads from
position, a glyph and text rather than from colour alone. The shipping app
signals that distinction with hue only (`.pill` / `.pill.up`, index.html:311-316),
which is invisible to a colour-blind reader.

The durable output of that session was not these files but an audit of the
real app — press-feedback coverage, render cost, and the contrast and type-size
findings. See the session notes rather than this folder.
