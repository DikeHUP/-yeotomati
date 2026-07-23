import asyncio
import random
import datetime
import os
from telethon import TelegramClient, events
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import ReactionEmoji
from telethon.errors import FloodWaitError

# ----- AYARLAR -----
API_ID = 31802611
API_HASH = '34659f5edc1ce2eb39d3d5c9b126af05'

# Session dosyası adı
SESSION_NAME = 'userbot_session'

# Script'in bulunduğu klasör (video yolunu buna göre sabitliyoruz)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VIDEO_PATH = os.path.join(BASE_DIR, 'davet.MP4')
VIDEO_TTL_SECONDS = 3   # video izlendikten sonra kaç saniyede silinsin (tek gösterimlik)

# KIZ HESABI İÇİN ÖZEL EMOJİ LİSTESİ
EMOJI_LIST = ['❤','🔥','🥰','💘','💔','💯','💋','🫶','🙈','💅','😘']

# Özel Mesaj Yanıtları
FIRST_REPLY_TEXT = """
Merhaba 😏 Hoş geldin canım!
Kız erkek karışık +18 sohbet grubumuza gelmek ister misin?
https://t.me/redcorner2 istersen bu linkten istersende arama yerine @redcorner2 yazarak aramıza katılabilirsin..
"""
SUBSEQUENT_REPLY_TEXT = "aşkım istek gönderdiysen en kısa zamanda onaylanacak merak etme💋💋💋"

# ----- ZAMAN AYARLARI -----
def get_delay():
    """Gece mi gündüz mü olduğuna göre bekleme süresi döndür"""
    now = datetime.datetime.now().hour
    if 9 <= now <= 23:  # Gündüz (09:00 - 23:00)
        return random.uniform(8, 15)  # 8-15 saniye
    else:  # Gece (23:00 - 09:00)
        return random.uniform(90, 120)  # 90-120 saniye

def should_react():
    """%25 ihtimalle emoji tepkisi ver (grup için)"""
    return random.random() < 0.25  # %25 ortalama

# -------------------
client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# Kullanıcı bazlı mesaj sayacı
user_message_count = {}
last_reply_time = {}
last_reaction_time = 0
min_reaction_interval = 15  # En az 15 saniye ara

@client.on(events.NewMessage(incoming=True))
async def handler(event):
    global last_reaction_time

    sender = await event.get_sender()
    if not sender:
        return

    # GÜVENLİ BOT KONTROLÜ
    is_bot = False
    try:
        if hasattr(sender, 'bot') and sender.bot:
            is_bot = True
    except Exception:
        pass

    if is_bot:
        print(f"[Filtre] Bot tespit edildi - İşlem yapılmadı.")
        return

    # ÖZEL MESAJ YANITI (SADECE İLK 2 MESAJA - %100 YANIT)
    if event.is_private:
        user_id = sender.id

        # Kullanıcının kaç mesaj yazdığını bul
        message_count = user_message_count.get(user_id, 0) + 1
        user_message_count[user_id] = message_count

        sender_name = getattr(sender, 'first_name', str(user_id))
        print(f"[Özel] {sender_name} - {message_count}. mesaj gönderdi.")

        # SADECE İLK 2 MESAJ İÇİN CEVAP VER (%100)
        if message_count <= 2:
            # Aynı kişiye spam yapmayı engelle
            now = asyncio.get_event_loop().time()
            last_time = last_reply_time.get(user_id, 0)

            if now - last_time < 25:  # 25 saniye
                print(f"[Özel] {sender_name} - Çok hızlı mesaj, atlanıyor.")
                return

            # Zaman bazlı bekleme
            delay = get_delay()
            await asyncio.sleep(delay)

            # Yazıyor gösterimi
            async with client.action(event.chat_id, 'typing'):
                await asyncio.sleep(random.uniform(1.5, 3.5))

            if message_count == 1:
                # 1. MESAJ: video (tek gösterimlik / self-destruct) + davet yazısı
                print(f"[Debug] Video aranıyor: {VIDEO_PATH} - var mı: {os.path.exists(VIDEO_PATH)}")
                if os.path.exists(VIDEO_PATH):
                    try:
                        await client.send_file(
                            event.chat_id,
                            VIDEO_PATH,
                            caption=FIRST_REPLY_TEXT,
                            ttl_seconds=VIDEO_TTL_SECONDS,
                            force_document=False,
                        )
                        print(f"[Özel] {sender_name} - 1. MESAJ (video, self-destruct) gönderildi ✅ ({delay:.1f}s)")
                    except Exception as e:
                        print(f"[HATA] Video gönderilemedi, metne düşülüyor: {e}")
                        await event.reply(FIRST_REPLY_TEXT)
                else:
                    print(f"[UYARI] Video bulunamadı: {VIDEO_PATH} - sadece metin gönderiliyor.")
                    await event.reply(FIRST_REPLY_TEXT)
            elif message_count == 2:
                # 2. MESAJ: ikinci mesaj
                await event.reply(SUBSEQUENT_REPLY_TEXT)
                print(f"[Özel] {sender_name} - 2. MESAJ yanıtı gönderildi ✅ ({delay:.1f}s)")

            last_reply_time[user_id] = now
        else:
            # 3. ve sonraki mesajlara HİÇBİR ŞEY YAPMA
            print(f"[Özel] {sender_name} - {message_count}. mesaj (CEVAP VERİLMEDİ, sessiz mod) ❌")
            return

    # GRUP MESAJLARINA RASTGELE EMOJİ TEPKİSİ (SADECE %25)
    elif event.is_group and not event.out:
        chat_title = getattr(event.chat, 'title', None) or 'Bilinmeyen grup'

        # Tepki verme oranı kontrolü (%25)
        if not should_react():
            print(f"[Grup] {chat_title} - Tepki verilmedi (oran atlandı).")
            return

        # Rate limit kontrolü
        now = asyncio.get_event_loop().time()
        if now - last_reaction_time < min_reaction_interval:
            remaining = min_reaction_interval - (now - last_reaction_time)
            print(f"[Grup] Rate limit - {remaining:.1f} saniye bekleniyor...")
            await asyncio.sleep(remaining)

        # Zaman bazlı bekleme
        delay = get_delay()
        await asyncio.sleep(delay)

        selected_emoji = random.choice(EMOJI_LIST)

        try:
            await client(SendReactionRequest(
                peer=event.chat_id,
                msg_id=event.message.id,
                reaction=[ReactionEmoji(emoticon=selected_emoji)]
            ))

            last_reaction_time = asyncio.get_event_loop().time()

            now_hour = datetime.datetime.now().hour
            mod = "GÜNDÜZ" if 9 <= now_hour <= 23 else "GECE"

            sender_name = getattr(sender, 'first_name', getattr(sender, 'title', 'Bilinmeyen'))
            print(f"[Grup][{mod}] {chat_title} - {sender_name} ➜ {selected_emoji} (+{delay:.1f}s)")

        except FloodWaitError as e:
            wait_time = e.seconds
            print(f"[FLOOD WAIT] {wait_time} saniye beklenmeli! Bot bekliyor...")
            await asyncio.sleep(wait_time + 10)
            try:
                await client(SendReactionRequest(
                    peer=event.chat_id,
                    msg_id=event.message.id,
                    reaction=[ReactionEmoji(emoticon=selected_emoji)]
                ))
                last_reaction_time = asyncio.get_event_loop().time()
                print(f"[Grup] Tekrar deneme başarılı!")
            except Exception as e2:
                print(f"[HATA] Tekrar deneme de başarısız: {e2}")

        except Exception as e:
            print(f"[HATA] Emoji eklenemedi: {e}")

async def main():
    print("=" * 55)
    print("🤖 Userbot başlatılıyor...")
    print(f"📁 Session dosyası: {SESSION_NAME}.session")
    print(f"📂 BASE_DIR: {BASE_DIR}")
    print(f"📂 BASE_DIR içeriği: {os.listdir(BASE_DIR)}")
    print(f"🎬 VIDEO_PATH: {VIDEO_PATH}")
    print(f"🎬 Video dosyası var mı: {os.path.exists(VIDEO_PATH)}")
    print("=" * 55)
    await client.start()
    print("✅ Userbot çalışıyor! 🚀")
    print("-" * 55)
    print("📅 ZAMAN MODU:")
    print("   ☀️ GÜNDÜZ (09:00-23:00): 8-15 saniye bekleme")
    print("   🌙 GECE  (23:00-09:00): 90-120 saniye bekleme")
    print("-" * 55)
    print("🎲 ORANLAR:")
    print("   🔸 Emoji tepkisi: %25 (her 4 mesajdan 1'ine)")
    print("   🔸 Özel mesaj yanıtı: %100 (ilk 2 mesaja)")
    print("   🔸 Rate limit aralığı: 15 saniye")
    print("-" * 55)
    print("💬 ÖZEL MESAJ:")
    print("   🔹 1. mesaj → VİDEO (tek gösterimlik, self-destruct) + davet yazısı")
    print("   🔹 2. mesaj → BEKLE mesajı (%100)")
    print("   🔹 3+ mesaj → SESSİZ (hiçbir şey yapma)")
    print("-" * 55)
    print("🛡️ KORUMALAR:")
    print("   🔸 FloodWait otomatik yönetimi")
    print("   🔸 Spam koruması (25 saniye)")
    print("   🔸 Bot filtresi aktif")
    print("=" * 55)
    await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())
