# Leaving feedback on Moviestar

If a command fails, a caption is hard to read, or your agent needs several
attempts to finish an edit, ask it to help you report what happened. You can
also tell us what worked well or suggest something you'd like Moviestar to do.
You don't need a Moviestar account to send feedback from the CLI.

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
automatically attach your video, project, logs, command history, or account
details. A successful response reports `status: "success"`; when the service
returns a `feedback_id`, keep it as a reference for your report.

If the command reports a failure, keep `feedback.md` and read the returned
error and retry instructions. A timeout does not prove the report wasn't
received. The feedback command has no retry key, so resubmitting after a lost
reply may send a duplicate report.

## Leave an email for follow up

If you'd like a note when the feedback is addressed, put your email under the
template's exact heading:

```markdown
## Follow-up email (optional)
you@example.com
```

Use your own address in place of the example. Moviestar removes this section
from the report text and sends the address separately for follow up. It does
not subscribe you to general update emails.

Leave the section blank to submit without a follow-up address. Moviestar adds
no account identity to the feedback, but names or other identifying details
you write in the report remain part of it.

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
