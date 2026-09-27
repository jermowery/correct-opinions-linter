# Correct Opinions Linter 🚨

A Discord bot that lints server messages and calls out anyone who violates the [Correct Opinions](https://www.correctopinions.info/).

Uses **Google Gemini 3.8 Flash** (free tier) to determine if a message contradicts any of the correct opinions. Opinions are **fetched live** from the website at startup and refreshed automatically every hour, so the bot always stays in sync.

## Example

> **User:** I love sparkling water, it's so refreshing!
>
> **Bot:** 🚨 **Correct Opinion Violation Detected!** 🚨
>
> • **#2**: *"Water should not taste like anything or be fizzy"*
>
> Please correct your opinions accordingly.

## Prerequisites

### 1. Create a Discord Bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Click **New Application** → give it a name → click **Create**
3. Go to the **Bot** tab → click **Reset Token** → copy the token
4. Under **Privileged Gateway Intents**, enable **Message Content Intent**
5. Go to the **OAuth2** tab → **URL Generator**:
   - Check **bot** under Scopes
   - Check **Send Messages** and **Read Message History** under Bot Permissions
6. Copy the generated URL and open it in your browser to invite the bot to your server

### 2. Get a Gemini API Key (Free)

1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey)
2. Click **Create API Key**
3. Copy the key

## Running with Docker (Recommended)

```bash
# Build the image
docker build -t correct-opinions-bot .

# Run the container
docker run -d \
  -e DISCORD_BOT_TOKEN="your-discord-bot-token" \
  -e GEMINI_API_KEY="your-gemini-api-key" \
  --name opinions-bot \
  --restart unless-stopped \
  correct-opinions-bot
```

### Optional Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DISCORD_BOT_TOKEN` | *(required)* | Your Discord bot token |
| `GEMINI_API_KEY` | *(required)* | Your Google Gemini API key |
| `OPINIONS_REFRESH_INTERVAL` | `3600` | How often (in seconds) to re-fetch opinions from the website |

### Managing the Container

```bash
# View logs
docker logs -f opinions-bot

# Stop the bot
docker stop opinions-bot

# Restart the bot
docker restart opinions-bot

# Remove the container
docker rm -f opinions-bot
```

## Running without Docker

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export DISCORD_BOT_TOKEN="your-discord-bot-token"
export GEMINI_API_KEY="your-gemini-api-key"

# Run the bot
python bot.py
```

## How It Works

1. On startup, the bot fetches the current list of correct opinions from [correctopinions.info](https://www.correctopinions.info/)
2. A background task re-fetches the opinions every hour (configurable) to pick up any changes
3. Every message in all channels the bot can see is sent to **Gemini 3.8 Flash**
4. Gemini determines if the message contradicts any opinion
5. If a violation is found, the bot replies to the message with the violated opinion(s)

## Rate Limits

The Gemini free tier allows **15 requests per minute** and **1,500 requests per day**. For a small-to-medium Discord server this is plenty. For very active servers, consider adding rate limiting or upgrading to a paid tier.
