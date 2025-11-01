# Anki CSV/TSV Importer

Imports a local or remote CSV/TSV file (including files stored in Google Sheets) into an Anki deck.

[![Version](https://img.shields.io/badge/version-v1.28.8-blue)](https://github.com/kardenwort/20250913122858-kardenwort)  [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

This script is designed for robustness and flexibility, featuring:
- **Batch Processing**: Reliably imports very large files by processing notes in chunks.
- **Dynamic Deck Assignment**: Automatically routes notes to different decks based on a `Deck` column in your source file.
- **Smart Updates**: Updates existing notes based on a unique field (prioritizing `Quotation`, then `Front`) without resetting scheduling.
- **Workflow Control**: An option to suspend all imported cards, allowing you to introduce them into your study queue at your own pace.

## Table of Contents

- [Anki CSV/TSV Importer](#anki-csvtsv-importer)
  - [Table of Contents](#table-of-contents)
  - [Usage](#usage)
  - [Instructions](#instructions)
    - [Using AnkiConnect (Recommended)](#using-ankiconnect-recommended)
    - [Without AnkiConnect](#without-ankiconnect)
  - [Getting the CSV URL for a Google Sheet](#getting-the-csv-url-for-a-google-sheet)
  - [File Format](#file-format)
  - [HTML Formatting](#html-formatting)
  - [How Sheet Modifications Are Handled](#how-sheet-modifications-are-handled)
  - [Notes](#notes)
  - [TODO](#todo)
  - [License](#license)

## Usage

The script is controlled via command-line arguments.

| Argument            | Description                                                                                                     |  Required   |
| ------------------- | --------------------------------------------------------------------------------------------------------------- | :---------: |
| `--path`, `-p`      | Path to the local CSV/TSV file. You must provide either `--path` or `--url`.                                    |     No      |
| `--url`, `-u`       | URL of the remote CSV file. You must provide either `--path` or `--url`.                                        |     No      |
| `--deck`, `-d`      | The default deck name to import notes into. Becomes optional if a `Deck` column is present in your source file. | Conditional |
| `--note`, `-n`      | The name of the Anki note type to use for all imported cards.                                                   |   **Yes**   |
| `--sync`, `-s`      | If present, automatically triggers an Anki synchronization after the import is complete.                        |     No      |
| `--suspend`         | If present, all newly added and updated cards will be suspended upon import.                                    |     No      |
| `--no-anki-connect` | A flag to bypass AnkiConnect and write directly to the Anki database. **Use with caution.**                     |     No      |
| `--col`, `-c`       | The full path to your `collection.anki2` file. **Required** if using `--no-anki-connect`.                       |     No      |
| `--allow-html`      | Renders HTML in fields instead of treating it as plain text. Only for use with `--no-anki-connect`.             |     No      |
| `--skip-header`     | Skips the first row of the source file. Only for use with `--no-anki-connect`.                                  |     No      |

[Back to Top](#table-of-contents)

## Instructions

### Using AnkiConnect (Recommended)

This is the safest and most powerful way to use the importer.

1.  Install the [AnkiConnect plugin](https://ankiweb.net/shared/info/2055492159) via Anki's add-on manager.
2.  Install Python 3 and `pip3`.
3.  Clone this repository (`git clone https://github.com/kardenwort/20250913123240-kardenwort-anki-csv-importer`).
4.  In your terminal, navigate to the repository folder and install dependencies: `pip3 install requests`.
5.  Make sure Anki **is running** in the background.
6.  Run the script from your terminal. All decks specified in your file or via the `--deck` argument will be created automatically if they don't exist.

**Example Commands:**

```bash
# Import from a local file, using a default deck
./anki-importer.py --path "/path/to/notes.tsv" --note "Basic" --deck "My Subject"

# Import from a remote URL, with dynamic decks defined in the file
./anki-importer.py --url "<published_google_sheet_url>" --note "Vocabulary"

# Import and suspend all new cards, then sync
./anki-importer.py --path "new_vocab.tsv" --note "Basic" --deck "Pending" --suspend --sync
```

### Without AnkiConnect

This method writes directly to Anki's database and carries significant risks:

1.  **No Auto-Sync**: Changes are local only. You must open Anki and sync manually to see them on other devices.
2.  **Risk of Data Corruption**: A version mismatch between the `anki` Python library and your Anki application can corrupt your collection. **Always back up your collection before using this method.**

If you accept these risks:

1.  Make sure Anki **is not running**.
2.  Install the required Python packages: `pip3 install -r requirements.txt`.
3.  Run the script with the `--no-anki-connect` and `--col` flags.

**To find your `collection.anki2` path:**
Refer to the [Anki Manual](https://docs.ankiweb.net/#/files?id=file-locations). For a profile named `User1` on macOS, the path is typically `~/Library/Application Support/Anki2/User1/collection.anki2`.

[Back to Top](#table-of-contents)

## Getting the CSV URL for a Google Sheet

To get a stable URL for a private or public sheet:

1.  Open your Google Sheet.
2.  Go to **File** -> **Share** -> **Publish to web**.
3.  In the dialog, under the "Link" tab, select the specific sheet you want to import.
4.  Choose **Comma-separated values (.csv)** from the dropdown menu.
5.  Click **Publish** and copy the generated URL. This is the URL to use with the `--url` argument.

[Back to Top](#table-of-contents)

## File Format

The script works best with a TSV (Tab-Separated Values) or CSV file that includes a header row.

The first row **must** be a header containing the exact field names of your Anki note type.

**Special Header Columns:**

*   `Deck` (Optional): Specifies the destination deck for that row. This allows you to import notes into multiple decks from a single file. If a value in this column is present, it overrides the `--deck` command-line argument for that note.
*   `Tags` (Optional): A space-separated list of tags to add to the note.

If a `Deck` column is not provided in your file, you **must** specify a default deck using the `--deck` argument.

**Example TSV File:**
```tsv
Quotation	Translation	Tags	Deck
"To be or not to be"	"Быть или не быть"	shakespeare classics	English::Literature
"Veni, vidi, vici"	"Пришел, увидел, победил"	latin history	Latin::Quotes
```

[Back to Top](#table-of-contents)

## HTML Formatting

HTML formatting is **always enabled** when using AnkiConnect.

When using the `--no-anki-connect` method, HTML is disabled by default. Use the `--allow-html` flag to enable it, but ensure your HTML is well-formed to avoid import errors.

To display HTML code as plain text (e.g., to show `<b>` on a card), use HTML entities: `&lt;b&gt;`.

[Back to Top](#table-of-contents)

## How Sheet Modifications Are Handled

The script intelligently handles changes to your source file:

*   **New Rows**: A new note is created in Anki.
*   **Modified Rows**: The script identifies existing notes by checking for a unique field, trying `Quotation` first, then falling back to `Front`. If a match is found, the note in Anki is updated with the new field content without affecting its review schedule.
*   **Deleted Rows**: Deleting a row in your source file **does not** delete the corresponding note from your Anki collection. This must be done manually within Anki.

[Back to Top](#table-of-contents)

## Notes

- **Batch Processing**: The script processes notes in batches of 100. This makes it highly reliable for importing thousands of notes at once without overwhelming AnkiConnect.
- **Media**: Importing media files (audio, images) is not currently supported.
- **Continuous Sync**: To keep a remote sheet synchronized with Anki, set up a cron job (macOS/Linux) or a Scheduled Task (Windows) to run this script at a regular interval.

[Back to Top](#table-of-contents)

## TODO

- [ ] Support for importing media files (audio, images).

[Back to Top](#table-of-contents)

## License

[MIT](./LICENSE)

[Back to Top](#table-of-contents)