# PureRef Connector for ComfyUI

Send built-in **Save Image** outputs to an editable PureRef board without adding nodes. Windows only; tested with PureRef 2.1.3.

## Install

Search for **PureRef Connector** in ComfyUI-Manager, or clone this repository into `ComfyUI/custom_nodes`. Restart ComfyUI and refresh the browser.

## Use

1. Click **PureRef** in the toolbar and enter an absolute `.pur` document path.
2. Check **Enable**, choose a save mode, and click **Apply**. Apply saves settings and closes the popup.

The connector opens its own editable PureRef window and creates missing documents. **Open board** applies the popup's settings and opens the board immediately. Close other PureRef windows using that document first. To switch documents, close the connector's current window.

- **PureRef + normal output folder** keeps the normal ComfyUI PNGs and embeds them in the board.
- **PureRef only** embeds outputs in the board and keeps temporary ComfyUI previews.
- Import or save failures keep outputs in ComfyUI's normal output folder and show a warning.
- **Enabled by Default** opts into enabling the connector when ComfyUI starts; otherwise startup is disabled.
- Choose whether closing PureRef reopens it for the next output or pauses the connector.

## Saving and layout

Before each update, the connector finishes any active note edit and saves the open board, including your references, notes, and rearrangements. Editing pauses briefly while PureRef's CLI loads the saved document, adds outputs at explicit coordinates, and saves it. The window then reloads the updated document. A temporary backup protects the original scene if the update fails.

**Images per row** defaults to **6** and is configurable in the popup. Separate generations continue filling the current row. Rows use 64-pixel spacing and account for different image sizes. The grid starts at the canvas origin and grows right and down; keep this area free of manually placed references. Moving existing outputs does not change where future slots are reserved.

The connector does not move the cursor, pan the canvas, open the hierarchy, create headers, or parse PureRef's database. PureRef restores its saved view when reloading. Updates also work with the connector window minimised. Open dialogs cause normal-folder fallback.

## Recover a workflow

Select an image in PureRef and use **Export selected images**, **Format: Original**, with **Export cropping off**. Drag the exported PNG into ComfyUI. Original exports preserve the PNG's workflow and prompt metadata; cropped or re-encoded exports do not. ComfyUI's `--disable-metadata` option still applies.

## Scope

One board shared across workflows. Only the built-in Save Image node is intercepted. Preview Image and third-party save nodes are unchanged. ComfyUI, its browser, and PureRef must run on the same Windows computer; open ComfyUI through `localhost` or `127.0.0.1`.

No added Python dependencies. Imports and coordinates use [PureRef's documented CLI](https://www.pureref.com/handbook/automation/); saving and reloading the editable window use targeted Windows keyboard messages without bringing it to the foreground. The dedicated PureRef profile disables analytics and update checks. Settings and reserved grid slots live in `ComfyUI/user/__pureref_connector`; temporary previews follow ComfyUI's normal cleanup.

Run checks with ComfyUI's Python: `python -m unittest discover -s tests -v`.
