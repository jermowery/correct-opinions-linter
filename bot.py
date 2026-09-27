"""
Correct Opinions Linter

Listens to every message in a Discord server and uses Google Gemini (free tier)
to determine if the message violates any of the "correct opinions" fetched live
from https://www.correctopinions.info/

When a violation is detected, the bot replies indicating which opinion was violated.
"""

import asyncio
import html
import os
import json
import logging
import re

import aiohttp
import discord
from google import genai

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

OPINIONS_URL = "https://www.correctopinions.info/"

# How often (in seconds) to re-fetch opinions from the website (default: 1 hour)
OPINIONS_REFRESH_INTERVAL = int(os.environ.get("OPINIONS_REFRESH_INTERVAL", "3600"))

if not DISCORD_TOKEN:
    raise RuntimeError(
        "Missing DISCORD_BOT_TOKEN environment variable. "
        "Create a bot at https://discord.com/developers/applications and set the token."
    )

if not GEMINI_API_KEY:
    raise RuntimeError(
        "Missing GEMINI_API_KEY environment variable. "
        "Get a free API key at https://aistudio.google.com/app/apikey"
    )

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("correct-opinions-bot")

# ---------------------------------------------------------------------------
# Dynamic opinion fetching
# ---------------------------------------------------------------------------

# Strings that appear as text in the page but are NOT opinions
_NOISE = {
    "Google Sites",
    "Report abuse",
    "Page details",
    "Page updated",
    "Embedded Files",
    "Skip to main content",
    "Skip to navigation",
    "Search this site",
    "Correct Opinions",
}


def _parse_opinions(page_html: str) -> dict[int, str]:
    """
    Extract the numbered list of correct opinions from the raw HTML of
    https://www.correctopinions.info/.

    The site is a Google Sites page — opinions are plain text nodes inside the
    main content area. We strip scripts/styles, pull out text fragments, and
    filter to just the opinion strings.
    """
    # Remove <script> and <style> blocks
    cleaned = re.sub(r"<script[^>]*>.*?</script>", "", page_html, flags=re.DOTALL)
    cleaned = re.sub(r"<style[^>]*>.*?</style>", "", cleaned, flags=re.DOTALL)

    # Extract text content between HTML tags (at least 10 chars to skip junk)
    raw_texts = re.findall(r">([^<]{10,})<", cleaned)

    opinions: dict[int, str] = {}
    capturing = False
    idx = 1

    for raw in raw_texts:
        text = html.unescape(raw.strip())

        # Start capturing after we see the "Correct Opinions" header
        if text == "Correct Opinions":
            capturing = True
            continue

        if not capturing:
            continue

        # Skip known non-opinion noise
        if text in _NOISE:
            continue

        # Skip anything that smells like CSS/JS remnants
        if any(kw in text for kw in ("rgba", "font-", "function ", "var ", "//")):
            continue

        opinions[idx] = text
        idx += 1

    return opinions


class OpinionStore:
    """Thread-safe store for the current set of correct opinions."""

    def __init__(self) -> None:
        self.opinions: dict[int, str] = {}
        self._lock = asyncio.Lock()

    async def refresh(self) -> None:
        """Fetch opinions from the website and update the store."""
        log.info("Fetching correct opinions from %s ...", OPINIONS_URL)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(OPINIONS_URL, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    resp.raise_for_status()
                    page_html = await resp.text()

            new_opinions = _parse_opinions(page_html)

            if not new_opinions:
                log.warning("Parsed 0 opinions — keeping previous list")
                return

            async with self._lock:
                self.opinions = new_opinions

            log.info("Loaded %d correct opinions", len(new_opinions))
            for num, text in new_opinions.items():
                log.debug("  #%d: %s", num, text)

        except Exception as e:
            log.error("Failed to fetch opinions: %s", e)
            if not self.opinions:
                raise RuntimeError(
                    f"Could not load initial opinions from {OPINIONS_URL}: {e}"
                ) from e

    async def get_snapshot(self) -> dict[int, str]:
        """Return a copy of the current opinions."""
        async with self._lock:
            return dict(self.opinions)

    def build_opinions_text(self, opinions: dict[int, str]) -> str:
        """Format opinions as a numbered list for the LLM prompt."""
        return "\n".join(f"  #{num}: {text}" for num, text in opinions.items())


opinion_store = OpinionStore()

# ---------------------------------------------------------------------------
# Gemini setup (free tier — gemini-3.8-flash)
# ---------------------------------------------------------------------------

gemini_client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT_TEMPLATE = """\
You are an opinion-violation detector. You will be given a Discord message and a \
numbered list of "correct opinions." Your job is to determine whether the message \
**contradicts or violates** any of the correct opinions.

Rules:
- A violation means the user's message expresses a view that is clearly the \
OPPOSITE of or CONTRADICTS one of the correct opinions.
- The message must have a clear, substantive connection to the opinion — not \
just mention the same topic. For example, merely mentioning "water" does not \
violate opinion #2 unless the user says something like "I love sparkling water" \
or "water should taste like something."
- If the message is ambiguous, short, or off-topic, respond with NO violation.
- Only flag clear, obvious violations. When in doubt, do NOT flag.
- A message can violate at most 3 opinions. Pick the strongest matches.

Correct Opinions:
{opinions_text}

Respond ONLY with valid JSON in one of these formats:

If there IS a violation:
{{"violation": true, "opinions": [<number>, ...], "reason": "<brief explanation>"}}

If there is NO violation:
{{"violation": false}}

Do NOT include any other text outside the JSON.
"""

# ---------------------------------------------------------------------------
# Discord bot
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True  # Required to read message text

client = discord.Client(intents=intents)


async def check_opinion_violation(
    message_text: str, opinions: dict[int, str]
) -> dict | None:
    """Send the message to Gemini and parse the JSON response."""
    if not message_text or len(message_text.strip()) < 3:
        return None

    opinions_text = opinion_store.build_opinions_text(opinions)
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(opinions_text=opinions_text)
    prompt = f'Discord message: """{message_text}"""'

    try:
        response = await gemini_client.aio.models.generate_content(
            model="gemini-3.8-flash",
            contents=system_prompt + "\n\n" + prompt,
            config=genai.types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=256,
                automatic_function_calling=genai.types.AutomaticFunctionCallingConfig(
                    disable=True,
                ),
            ),
        )

        raw = response.text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        result = json.loads(raw)
        return result

    except json.JSONDecodeError:
        log.warning("Failed to parse Gemini response as JSON: %s", raw)
        return None
    except Exception as e:
        log.error("Error calling Gemini API: %s", e)
        return None


async def _periodic_refresh():
    """Background task that re-fetches opinions on a schedule."""
    await client.wait_until_ready()
    while not client.is_closed():
        await asyncio.sleep(OPINIONS_REFRESH_INTERVAL)
        await opinion_store.refresh()


@client.event
async def on_ready():
    log.info("Bot is online as %s (ID: %s)", client.user, client.user.id)
    opinions = await opinion_store.get_snapshot()
    log.info("Monitoring %d correct opinions", len(opinions))


@client.event
async def on_message(message: discord.Message):
    # Ignore messages from the bot itself
    if message.author == client.user:
        return

    # Ignore messages from other bots
    if message.author.bot:
        return

    # Ignore empty messages (e.g., image-only)
    if not message.content:
        return

    # Ignore channels that are for games, not opinions
    channel_name = getattr(message.channel, "name", "") or ""
    if "wordle-and-other-games" in channel_name:
        return

    opinions = await opinion_store.get_snapshot()
    if not opinions:
        return

    log.info(
        "Checking message from %s in #%s: %s",
        message.author,
        getattr(message.channel, "name", "DM"),
        message.content[:100],
    )

    result = await check_opinion_violation(message.content, opinions)

    if result and result.get("violation"):
        opinion_numbers = result.get("opinions", [])
        violated = []
        for num in opinion_numbers:
            if num in opinions:
                violated.append(f"**#{num}**: *\"{opinions[num]}\"*")

        if violated:
            violations_str = "\n".join(f"• {v}" for v in violated)
            reply = (
                f"🚨 **Correct Opinion Violation Detected!** 🚨\n\n"
                f"{violations_str}\n\n"
                f"Please correct your opinions accordingly."
            )
            log.info("Violation detected for %s: %s", message.author, opinion_numbers)
            await message.reply(reply)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    # Fetch opinions before starting the bot so we fail fast if the site is down
    await opinion_store.refresh()

    async with client:
        client.loop.create_task(_periodic_refresh())
        await client.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
