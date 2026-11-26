# Leaving feedback on Moviestar

If a command fails, a caption is hard to read, or your agent needs several
attempts to finish an edit, ask it to help you report what happened. You can
also tell us what worked well or suggest something you'd like Moviestar to do.
You don't need a Moviestar account to send feedback from the CLI.

The connected-contact and anonymous controls below require Moviestar 0.8.4 or
newer. Check `moviestar --version` and upgrade an older installation before using
these examples.

## Ask your agent to write the report

For example, after reviewing a video:

> Help me leave feedback for the Moviestar team. The captions in the last five
> seconds overlap the speaker's face. Describe what we were trying to make,
> what we saw, and how we worked around it. Show me the report before sending it.

Or, after something goes well:

> Draft feedback for Moviestar: emailing the video link saved me from uploading
> it elsewhere and writing a separate email. Include what we did and show me
> the report before sending it.

Your agent can use the commands and results from your conversation to draft
the report. Have it distinguish what it actually observed from what you told
it. For example, if you noticed the caption overlap, it should say that you
reported it unless the agent also inspected the frame.

## Fill in the feedback template

To create an editable report, your agent runs:

```sh
moviestar feedback > feedback.md
```

This writes a Markdown template to `feedback.md`. It sends nothing to the
Moviestar team. Choose another filename if you already have a report with
that name: shell redirection replaces an existing file.

The template starts with three questions:

- **Summary:** what should we know in one sentence?
- **What I was trying to do:** what result did you want?
- **Feedback:** what happened, or what would you like to change?

Here's an illustrative report for the caption example:

```markdown
## Summary (required)
Captions covered the speaker's face near the end of my clip.

## What I was trying to do (required)
Make a 30-second vertical interview clip with captions near the bottom.

## Feedback (required)
When I watched the exported MP4, the captions overlapped the speaker's face
in the last five seconds. The speaker had leaned down in the frame.

## Expected behavior (optional)
I'd like a way to move the captions for just that part of the clip.

## Impact or workaround (optional)
We moved captions higher for the whole video, but I preferred the original
position for the first 25 seconds.
```

Replace the example with what happened in your session. If you have a command
that reproduces the problem or a relevant error message, add it under
**Reproduction and evidence**. Include only the details needed to explain the
problem.

You can also fill in **What worked well**, **Ideas**, or **Wild idea**. The
template asks how likely you are to recommend Moviestar on a scale from 0 to
10, and why. Those sections are optional; leave them blank or remove them if
you have nothing to add.

## Review and send it

Read the report before sending. Remove private file paths, video links, names,
credentials, or transcript excerpts you don't want to share. The CLI sends the
text you put in the report, so anything pasted into it will be submitted.

When you're ready, tell your agent:

> Send the feedback report we just reviewed to the Moviestar team.

Or submit it yourself:

```sh
moviestar feedback --file feedback.md
```

The CLI sends the report text and your installed Moviestar version. It does not
automatically attach your video, project, logs, or command history.

By default, a connected submission uses your approved account connection. The
service can retain verified account contact for that report; the CLI does not
search your device for an email address. A blank follow-up email section does
not make a connected report anonymous. Use the explicit anonymous option below
if you do not want account contact attached.

A successful response reports `status: "success"`. A confirmed connected receipt
also reports `account_linked` and `contact_available` without exposing your
address, account ID, or credential. Keep any returned `feedback_id` as the
reference for your report. An older service may not confirm account linkage;
read the receipt rather than assuming it did.

If the command reports a failure, keep `feedback.md`, any `feedback_id`, and the
returned error/recovery instructions. A timeout does not prove the report wasn't
received. For an uncertain connected submission, `anonymous: null` means
anonymity is unconfirmed: the report may already include account contact.
Authentication failures do not trigger an anonymous retry. The command has no
retry key, so do not automatically resend or switch to anonymous after a lost
reply; that can create a duplicate report.

## Leave an email for follow up

If you'd like a note when the feedback is addressed, put your email under the
template's exact heading:

```markdown
## Follow-up email (optional)
you@example.com
```

Use your own address in place of the example. Moviestar removes this section
from the report text and sends the address separately as a deliberately supplied
alternative contact. It is not treated as your verified account address and does
not replace any account contact already resolved by the service. Contact capture
does not subscribe you to updates or send an automatic follow-up message.

## Submit without account contact

To omit the saved account connection and its verified contact, use:

```sh
moviestar feedback --anonymous --file feedback.md
```

`--anonymous` skips account-state and credential access. Leave the optional
follow-up email section blank, and remove names or other identifying details
from the report if you do not want to share them. Anything you write in the
report remains part of it. Logged-out submissions have no account contact;
a deliberately supplied follow-up address is still sent separately.

See [Feedback and account contact](feedback.md) for the receipt and recovery
contract.

## Send a short note

For a single observation that needs no longer report, ask:

> Send this feedback to Moviestar: the preview made it easy to catch a bad cut
> before the final export.

Your agent can use:

```sh
moviestar feedback --quick "The preview made it easy to catch a bad cut before the final export."
```

`--quick` sends the note immediately. To prepare a report without sending it,
use the template command instead. Run `moviestar feedback --help` for all
feedback options.
