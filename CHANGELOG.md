# Changelog

Build numbers have the form `YYYY-MM-DD#NN`, as in the Redactor apps. Each check-in that changes real code runs `python bump_version.py` and adds a section here.

## 2026-10-28#01 -- The rules engine and cron time windows (no window yet)

- **Rules decide where a download goes.** An ordered list; a rule whose conditions all match sets a folder, adds a subfolder, renames the file and/or queues a command, then stops or lets the rules below add to it (a domain rule sets the base folder, a type rule adds the subfolder).
- **Conditions:** domain (of the file or of the page it was clicked on; `example.com` covers its subdomains), file type (groups such as Video or Ebooks, extensions, MIME types; a known extension wins over the MIME type the server sends), name (wildcards or regular expressions), size, time, direct download or video site, and the video's channel, uploader, playlist or title.
- **Time windows are cron lines underneath.** The picker's "weekdays 08:30–16:15" becomes exact cron lines, and lines read back as a sentence ("Weekdays, 08:30–16:15"). A window past midnight carries on into the next day. Unlike classic cron, a day-of-month AND a weekday must both match, so "Monday, the 1st to the 7th" means the first Monday.
- **Folder tokens** `{yyyy} {MM} {dd} {week} {weekday} {domain} {pagedomain} {name} {ext} {channel} {uploader} {playlist} {title}` make folders rotate by date. A token's value is always one safe folder name: a channel called "AC/DC" or ".." cannot climb out of its folder, and Windows' reserved names are avoided.
- **"Why here?"** lists the rules that hit and what each did; the Rule column shows them as `1+5`. Name clashes become `name (1).ext`, never an overwrite.
- **The default rule set** sorts video sites by uploader, then Video, Audio, Images, Books (ebooks and comics), Documents, Archives, Programs, and Other.
- Rules are stored as JSON; keys from a newer version are skipped rather than refused. The editor can ask a rule what is wrong with it in plain words.
