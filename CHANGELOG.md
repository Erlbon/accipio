# Changelog

Build numbers have the form `YYYY-MM-DD#NN`, as in the Redactor apps. Each check-in that changes real code runs `python bump_version.py` and adds a section here.

## 2026-10-28#02 -- The download engine (aria2) and the link check (no window yet)

- **aria2 runs the direct downloads.** Accipio starts its own aria2c on 127.0.0.1 with a random port and secret, and aria2c stops by itself if Accipio crashes. Downloads go to the folder and name the rules chose; the folder is created when needed. Pause, resume, remove, the number of downloads at once and a total speed limit work while downloads run. Unfinished downloads are saved and come back after a restart.
- **Nothing is ever overwritten.** A file that is already there makes the download fail instead. aria2's own "continue" mode stays off: with it on, aria2 took a file you already had for a half-finished download and wrote the new file into it (caught by a test before any release).
- **Servers that cannot resume:** resuming one after a pause fails with "resume not supported"; the engine can then start it over from zero, removing only its own half-finished file.
- **Closing is instant:** aria2c on Windows takes about four seconds to exit, so Accipio saves the queue first and lets aria2 finish on its own.
- **The link check** asks the server, before downloading, for the real file name, type and size: the name from the server's Content-Disposition (including UTF-8 names like `æøå.epub`), else from the address after redirects. A script link gets the extension of what it serves (`get.php` that is a zip becomes `get.zip`), and a name with folders in it is cut to the name. Servers that refuse a HEAD request are asked for one byte instead.
- `python tools/fetch_engines.py` fetches aria2 1.37.0 from its official release into `bin/`, checked against a pinned SHA-256, with its licence texts.

## 2026-10-28#01 -- The rules engine and cron time windows (no window yet)

- **Rules decide where a download goes.** An ordered list; a rule whose conditions all match sets a folder, adds a subfolder, renames the file and/or queues a command, then stops or lets the rules below add to it (a domain rule sets the base folder, a type rule adds the subfolder).
- **Conditions:** domain (of the file or of the page it was clicked on; `example.com` covers its subdomains), file type (groups such as Video or Ebooks, extensions, MIME types; a known extension wins over the MIME type the server sends), name (wildcards or regular expressions), size, time, direct download or video site, and the video's channel, uploader, playlist or title.
- **Time windows are cron lines underneath.** The picker's "weekdays 08:30–16:15" becomes exact cron lines, and lines read back as a sentence ("Weekdays, 08:30–16:15"). A window past midnight carries on into the next day. Unlike classic cron, a day-of-month AND a weekday must both match, so "Monday, the 1st to the 7th" means the first Monday.
- **Folder tokens** `{yyyy} {MM} {dd} {week} {weekday} {domain} {pagedomain} {name} {ext} {channel} {uploader} {playlist} {title}` make folders rotate by date. A token's value is always one safe folder name: a channel called "AC/DC" or ".." cannot climb out of its folder, and Windows' reserved names are avoided.
- **"Why here?"** lists the rules that hit and what each did; the Rule column shows them as `1+5`. Name clashes become `name (1).ext`, never an overwrite.
- **The default rule set** sorts video sites by uploader, then Video, Audio, Images, Books (ebooks and comics), Documents, Archives, Programs, and Other.
- Rules are stored as JSON; keys from a newer version are skipped rather than refused. The editor can ask a rule what is wrong with it in plain words.
