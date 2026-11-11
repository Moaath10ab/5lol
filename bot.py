import asyncio
import os
import tempfile
from collections import defaultdict

import discord
from discord.ext import commands
from google import genai
from gtts import gTTS


DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not DISCORD_BOT_TOKEN:
    raise RuntimeError("متغير البيئة DISCORD_BOT_TOKEN غير مضبوط.")
if not GEMINI_API_KEY:
    raise RuntimeError("متغير البيئة GEMINI_API_KEY غير مضبوط.")

gemini_client = genai.Client(api_key=GEMINI_API_KEY)

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)
guild_locks = defaultdict(asyncio.Lock)


def get_gemini_reply(prompt: str) -> str:
    """إرسال السؤال إلى Gemini وإرجاع الرد كنص."""
    response = gemini_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=(
            "أجب باللغة العربية وبأسلوب واضح ومختصر، لأن إجابتك ستُقرأ صوتيًا.\n\n"
            f"السؤال: {prompt}"
        ),
    )
    return (response.text or "").strip()


def create_mp3(text: str, file_path: str) -> None:
    """تحويل النص العربي إلى ملف MP3."""
    gTTS(text=text, lang="ar").save(file_path)


async def speak_in_voice_channel(ctx: commands.Context, text: str) -> None:
    """تشغيل ملف MP3 في الروم الصوتي الخاص بصاحب الأمر."""
    author_voice = getattr(ctx.author, "voice", None)
    if author_voice is None or author_voice.channel is None:
        await ctx.send("يجب أن تدخل رومًا صوتيًا أولًا.")
        return

    voice_channel = author_voice.channel
    voice_client = ctx.voice_client

    if voice_client is None:
        voice_client = await voice_channel.connect()
    elif voice_client.channel != voice_channel:
        await voice_client.move_to(voice_channel)

    temp_file = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    file_path = temp_file.name
    temp_file.close()

    try:
        await asyncio.to_thread(create_mp3, text, file_path)

        # يتطلب تشغيل هذا الجزء وجود FFmpeg مثبتًا على بيئة الاستضافة.
        audio_source = discord.FFmpegPCMAudio(file_path)
        finished = asyncio.Event()
        loop = asyncio.get_running_loop()

        def after_playback(error: Exception | None) -> None:
            if error:
                print(f"خطأ أثناء تشغيل الصوت: {error}")
            loop.call_soon_threadsafe(finished.set)

        voice_client.play(audio_source, after=after_playback)
        await asyncio.wait_for(finished.wait(), timeout=300)

    finally:
        if voice_client.is_playing():
            voice_client.stop()

        try:
            os.remove(file_path)
        except FileNotFoundError:
            pass


@bot.event
async def on_ready() -> None:
    print(f"تم تسجيل الدخول باسم: {bot.user}")


@bot.command(name="talk")
@commands.guild_only()
async def talk(ctx: commands.Context, *, prompt: str = "") -> None:
    """الاستخدام: !talk اكتب سؤالك هنا"""
    prompt = prompt.strip()

    if not prompt:
        await ctx.send("اكتب سؤالك بعد الأمر، مثل: `!talk ما عاصمة المغرب؟`")
        return

    if len(prompt) > 1000:
        await ctx.send("الرجاء اختصار السؤال إلى 1000 حرف أو أقل.")
        return

    # منع تداخل عدة طلبات صوتية في الخادم نفسه.
    async with guild_locks[ctx.guild.id]:
        status_message = await ctx.send("🤖 أفكر في إجابتك...")

        try:
            reply = await asyncio.to_thread(get_gemini_reply, prompt)
            if not reply:
                await status_message.edit(content="لم أتمكن من الحصول على إجابة هذه المرة.")
                return

            # حدّ النص لتجنب تجاوز حد رسائل Discord.
            await status_message.edit(content=f"🤖 {reply[:1800]}")

            # حدّ النص المنطوق لتقليل طول الملف الصوتي.
            spoken_reply = reply[:1200]
            await speak_in_voice_channel(ctx, spoken_reply)

        except asyncio.TimeoutError:
            await ctx.send("استغرق تشغيل الصوت وقتًا أطول من المتوقع.")
        except discord.ClientException as error:
            await ctx.send(
                f"تعذّر تشغيل الصوت. تأكد من تثبيت FFmpeg ومن صلاحيات البوت الصوتية. "
                f"التفاصيل: `{error}`"
            )
        except Exception as error:
            print(f"حدث خطأ في الأمر !talk: {error}")
            await ctx.send("حدث خطأ أثناء تجهيز الإجابة أو تشغيلها. حاول مرة أخرى.")


@talk.error
async def talk_error(ctx: commands.Context, error: commands.CommandError) -> None:
    if isinstance(error, commands.NoPrivateMessage):
        await ctx.send("هذا الأمر متاح داخل خادم ديسكورد فقط.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("اكتب سؤالك بعد الأمر، مثل: `!talk مرحبًا`")
    else:
        print(f"خطأ في الأمر !talk: {error}")
        await ctx.send("تعذّر تنفيذ الأمر. تحقق من صلاحيات البوت وإعداداته.")


bot.run(DISCORD_BOT_TOKEN)
