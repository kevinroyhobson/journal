# journal

An LLM-powered journaling TUI. Talk through your day with an AI, then generate an encrypted journal entry from the conversation.

## Setup

```
pip install -e .
journal
```

Requires either [Ollama](https://ollama.com) running locally or an Anthropic API key.

## Configuration

`~/.config/journal/config.json`:

```json
{
  "provider": "anthropic",
  "s3_bucket": "your-bucket-name"
}
```

Entries are encrypted with a passphrase and stored in S3.
