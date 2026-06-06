#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TELEGRAM OTOMATİK KULLANICI TOPLAYICI VE EKLEYİCİ
- Database'li (PostgreSQL)
- DM komut sistemi ile uzaktan kontrol
- Otomatik zamanlanmış keşif ve ekleme
"""

import asyncio
import os
import json
from datetime import datetime, time
from telethon import TelegramClient
from telethon.errors import FloodWaitError, UserPrivacyRestrictedError, UserIdInvalidError
from telethon.tl.functions.messages import GetHistoryRequest
from telethon.tl.functions.channels import InviteToChannelRequest
from telethon.tl.types import InputUser
from telethon import events
import asyncpg

# ==================== KONFIGÜRASYON ====================
API_ID = 33345764
API_HASH = '8576bb618f0b33ea7b15c1c249d19e28'
PHONE_NUMBER = '+99361023990'

HEDEF_GRUP = '@c2redcorner'
YETKILI_HESAP = 'merhababendike'  # Sadece bu hesaptan gelen komutlar çalışır
RAPOR_HESAP = 'merhababendike'    # Raporların gönderileceği hesap

KESIF_SAATLERI = [12, 18, 23]  # 12:00, 18:00, 23:00
EKLEME_PENCERESI = (0, 30)  # 00:00 - 00:30
GUNLUK_LIMIT = 40
BEKLEME_SANİYE = 3

SESSION_NAME = 'telegram_otomatik'

# ==================== VERİTABANI ====================
# !!! BURAYA KENDİ DATABASE URL'İNİ YAZ !!!
DATABASE_URL = 'postgresql://otomat_database_user:ztcFFMeJq6AzQnndkVIoK2TmUwhI94HO@dpg-d8i3rg6q1p3s73eajeig-a/otomat_database'

# ==================== VERİTABANI SINIFI ====================

class Database:
    def __init__(self):
        self.pool = None
    
    async def init(self):
        """Bağlantı havuzu oluştur ve tabloları hazırla"""
        self.pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
        
        async with self.pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id BIGINT PRIMARY KEY,
                    access_hash BIGINT,
                    username TEXT,
                    name TEXT,
                    last_seen TEXT,
                    group_name TEXT,
                    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_added BOOLEAN DEFAULT FALSE,
                    added_at TIMESTAMP
                )
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS daily_stats (
                    date DATE PRIMARY KEY,
                    added_count INTEGER DEFAULT 0
                )
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS target_members (
                    user_id BIGINT PRIMARY KEY,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            print("✅ Veritabanı tabloları hazır")
    
    async def add_user(self, user_id, access_hash, username, name, last_seen, group_name):
        """Kullanıcı ekle (tekilleştirilmiş)"""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO users (id, access_hash, username, name, last_seen, group_name)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (id) DO UPDATE SET
                    last_seen = EXCLUDED.last_seen,
                    group_name = EXCLUDED.group_name
            ''', user_id, access_hash, username[:100] if username else '', 
                name[:100] if name else '-', last_seen[:50], group_name[:100])
    
    async def get_pending_users(self, limit):
        """Eklenmemiş kullanıcıları getir"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT id, access_hash, username 
                FROM users 
                WHERE is_added = FALSE 
                LIMIT $1
            ''', limit)
            return [(row['id'], row['access_hash']) for row in rows]
    
    async def mark_as_added(self, user_ids):
        """Kullanıcıları eklendi olarak işaretle"""
        if not user_ids:
            return
        async with self.pool.acquire() as conn:
            await conn.execute('''
                UPDATE users 
                SET is_added = TRUE, added_at = CURRENT_TIMESTAMP 
                WHERE id = ANY($1::bigint[])
            ''', user_ids)
    
    async def get_today_added_count(self):
        """Bugün eklenen kişi sayısı"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT added_count FROM daily_stats 
                WHERE date = CURRENT_DATE
            ''')
            return row['added_count'] if row else 0
    
    async def increment_today_added(self, count):
        """Bugün eklenen sayısını artır"""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO daily_stats (date, added_count)
                VALUES (CURRENT_DATE, $1)
                ON CONFLICT (date) DO UPDATE SET
                    added_count = daily_stats.added_count + $1
            ''', count)
    
    async def is_in_target_group(self, user_id):
        """Kullanıcı hedef grupta mı?"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT 1 FROM target_members WHERE user_id = $1', user_id)
            return row is not None
    
    async def add_target_member(self, user_id):
        """Hedef gruba üye ekle"""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO target_members (user_id) VALUES ($1)
                ON CONFLICT (user_id) DO NOTHING
            ''', user_id)
    
    async def get_pending_count(self):
        """Eklenmemiş kullanıcı sayısı"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT COUNT(*) FROM users WHERE is_added = FALSE')
            return row[0]
    
    async def get_total_users(self):
        """Toplam kullanıcı sayısı"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT COUNT(*) FROM users')
            return row[0]
    
    async def load_target_group_members(self, client, group_entity):
        """Hedef grubun mevcut üyelerini 'eklendi' olarak işaretle"""
        print("📋 Hedef grup üyeleri taranıyor...")
        count = 0
        
        async with self.pool.acquire() as conn:
            async for member in client.iter_participants(group_entity):
                if hasattr(member, 'bot') and member.bot:
                    continue
                
                await conn.execute('''
                    INSERT INTO target_members (user_id) VALUES ($1)
                    ON CONFLICT (user_id) DO NOTHING
                ''', member.id)
                
                await conn.execute('''
                    UPDATE users 
                    SET is_added = TRUE, added_at = CURRENT_TIMESTAMP 
                    WHERE id = $1 AND is_added = FALSE
                ''', member.id)
                
                count += 1
                if count % 100 == 0:
                    print(f"   {count} üye işlendi...")
        
        print(f"✅ {count} mevcut üye 'eklendi' olarak işaretlendi")
        return count

# ==================== KEŞİF MODÜLÜ ====================

class GroupUserScanner:
    def __init__(self, client, db):
        self.client = client
        self.db = db
        self.admin_cache = {}
        
    def format_last_seen(self, status):
        """Son görülme formatla"""
        if status is None:
            return "Uzun zaman önce"
        elif hasattr(status, 'was_online'):
            now = datetime.now(status.date.tzinfo)
            diff = now - status.date
            if diff.days > 0:
                return f"{diff.days} gün önce"
            elif diff.seconds > 3600:
                return f"{diff.seconds // 3600} saat önce"
            elif diff.seconds > 60:
                return f"{diff.seconds // 60} dakika önce"
            else:
                return "Şimdi"
        return "Bilinmiyor"
    
    async def is_user_admin(self, group, user_id):
        """Admin kontrolü"""
        cache_key = f"{group.id}_{user_id}"
        if cache_key in self.admin_cache:
            return self.admin_cache[cache_key]
        
        try:
            async for participant in self.client.iter_participants(group):
                if participant.id == user_id:
                    from telethon.tl.types import ChannelParticipantAdmin, ChannelParticipantCreator
                    if hasattr(participant, 'participant') and isinstance(participant.participant, (ChannelParticipantAdmin, ChannelParticipantCreator)):
                        self.admin_cache[cache_key] = True
                        return True
                    break
        except:
            pass
        
        self.admin_cache[cache_key] = False
        return False
        
    async def scan_group_messages(self, group):
        """Grup mesajlarını tarar"""
        # Hedef grup kontrolü
        target_entity = await self.client.get_entity(HEDEF_GRUP)
        if group.id == target_entity.id:
            print(f"  ⏭️ Hedef grup atlandı: {group.title}")
            return 0
        
        print(f"  📁 {group.title}")
        
        try:
            history = await self.client(GetHistoryRequest(
                peer=group,
                limit=2500,
                offset_date=None,
                offset_id=0,
                max_id=0,
                min_id=0,
                add_offset=0,
                hash=0
            ))
            
            users_found = set()
            for message in history.messages:
                if message.sender_id:
                    users_found.add(message.sender_id)
                if message.reply_to_msg_id:
                    try:
                        reply_msg = await self.client.get_messages(group, ids=message.reply_to_msg_id)
                        if reply_msg and reply_msg.sender_id:
                            users_found.add(reply_msg.sender_id)
                    except:
                        pass
            
            new_users = 0
            for user_id in users_found:
                try:
                    # Admin mi?
                    if await self.is_user_admin(group, user_id):
                        continue
                    
                    user = await self.client.get_entity(user_id)
                    
                    # Bot mu?
                    if hasattr(user, 'bot') and user.bot:
                        continue
                    
                    # Hedef grupta mı?
                    if await self.db.is_in_target_group(user.id):
                        continue
                    
                    username = getattr(user, 'username', '')
                    name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
                    last_seen = self.format_last_seen(getattr(user, 'status', None))
                    
                    await self.db.add_user(
                        user.id, 
                        getattr(user, 'access_hash', 0),
                        username or '', 
                        name or '-', 
                        last_seen, 
                        group.title
                    )
                    new_users += 1
                        
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds)
                except Exception:
                    continue
                    
            print(f"     📊 {new_users} yeni kullanıcı")
            return new_users
            
        except FloodWaitError as e:
            print(f"  🚫 FloodWait: {e.seconds} saniye")
            await asyncio.sleep(e.seconds)
            return 0
        except Exception as e:
            print(f"  ❌ Hata: {type(e).__name__}")
            return 0
            
    async def scan_all_groups(self):
        """Tüm grupları tara"""
        start_time = datetime.now()
        print(f"\n🔍 Tarama başladı: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        try:
            dialogs = await self.client.get_dialogs()
            groups = [d for d in dialogs if d.is_group or d.is_channel]
            print(f"📡 {len(groups)} grup/kanal bulundu")
            
            total_new = 0
            
            for i, group in enumerate(groups, 1):
                print(f"\n[{i}/{len(groups)}]", end=" ")
                new_count = await self.scan_group_messages(group.entity)
                total_new += new_count
                await asyncio.sleep(2)
            
            pending = await self.db.get_pending_count()
            total = await self.db.get_total_users()
            rapor = f"📊 {start_time.strftime('%H:%M')} taraması tamamlandı\n"
            rapor += f"{total_new} yeni kullanıcı bulundu\n"
            rapor += f"📦 Bekleyen: {pending} kişi (Toplam: {total})"
            
            await self.send_report(rapor)
            print(f"\n✅ Tarama tamamlandı: +{total_new} yeni (Toplam: {total})")
            return total_new
            
        except Exception as e:
            print(f"🔥 Hata: {type(e).__name__} - {e}")
            return 0
            
    async def send_report(self, message):
        """Rapor gönder"""
        try:
            entity = await self.client.get_entity(RAPOR_HESAP)
            await self.client.send_message(entity, message)
            print("📤 Rapor gönderildi")
        except Exception as e:
            print(f"Rapor gönderilemedi: {e}")

# ==================== EKLEYİCİ MODÜLÜ ====================

class GroupAdder:
    def __init__(self, client, db):
        self.client = client
        self.db = db
        self.is_running = False
        
    async def add_users_to_group(self, group_entity, users):
        """Kullanıcıları gruba ekler"""
        success_list = []
        error_count = 0
        
        for user_id, hash_val in users:
            try:
                input_user = InputUser(user_id=user_id, access_hash=hash_val)
                await self.client(InviteToChannelRequest(
                    channel=group_entity,
                    users=[input_user]
                ))
                print(f"  ✓ {user_id} eklendi")
                success_list.append(user_id)
                
            except FloodWaitError as e:
                print(f"  ⏳ FloodWait: {e.seconds} saniye")
                if e.seconds < 60:
                    await asyncio.sleep(e.seconds)
                    try:
                        input_user = InputUser(user_id=user_id, access_hash=hash_val)
                        await self.client(InviteToChannelRequest(channel=group_entity, users=[input_user]))
                        success_list.append(user_id)
                    except:
                        error_count += 1
                else:
                    print(f"  ✗ {user_id} - Bekleme süresi çok uzun, atlanıyor")
                    error_count += 1
                    
            except UserPrivacyRestrictedError:
                print(f"  ✗ {user_id} gizlilik engeli")
                error_count += 1
                
            except UserIdInvalidError:
                print(f"  ✗ {user_id} geçersiz ID")
                error_count += 1
                
            except Exception as e:
                error_msg = str(e)
                if "CHAT_ADMIN_REQUIRED" in error_msg:
                    print(f"  ❌ ADMIN YETKİN YOK! Lütfen {HEDEF_GRUP} grubunda manuel olarak admin yap")
                    return success_list, error_count
                print(f"  ✗ {user_id} hata: {error_msg[:50]}")
                error_count += 1
            
            await asyncio.sleep(BEKLEME_SANİYE)
        
        return success_list, error_count
    
    async def run_adder(self, custom_limit=None):
        """Ana ekleme fonksiyonu"""
        if self.is_running:
            return "⚠️ Zaten bir ekleme işlemi devam ediyor!"
        
        self.is_running = True
        print(f"\n🌟 EKLEYİCİ BAŞLADI - {datetime.now().strftime('%H:%M:%S')}")
        
        try:
            # Günlük limit kontrolü
            bugun_eklenen = await self.db.get_today_added_count()
            kullanilacak_limit = custom_limit if custom_limit else GUNLUK_LIMIT
            
            if bugun_eklenen >= kullanilacak_limit:
                msg = f"⚠️ Günlük limit aşıldı ({kullanilacak_limit}/{kullanilacak_limit})"
                print(msg)
                await self.send_report(msg)
                return msg
            
            kalan_kota = kullanilacak_limit - bugun_eklenen
            print(f"📊 Bugün eklenen: {bugun_eklenen}, Kalan kota: {kalan_kota}")
            
            # Hedef grubu al
            try:
                group_entity = await self.client.get_entity(HEDEF_GRUP)
                print(f"🎯 Hedef grup: {group_entity.title}")
            except Exception as e:
                print(f"❌ Hedef grup bulunamadı: {e}")
                await self.send_report(f"❌ Hedef grup bulunamadı: {HEDEF_GRUP}")
                return f"Hata: Hedef grup bulunamadı"
            
            # Eklenmemiş kullanıcıları al
            pending_users = await self.db.get_pending_users(kalan_kota)
            
            if not pending_users:
                print("✅ Eklenecek kullanıcı yok!")
                return "✅ Eklenecek kullanıcı yok!"
            
            print(f"📋 {len(pending_users)} kişi eklenecek")
            
            # Ekleme işlemini yap
            added_users, errors = await self.add_users_to_group(group_entity, pending_users)
            
            if added_users:
                await self.db.mark_as_added(added_users)
                await self.db.increment_today_added(len(added_users))
            
            # Rapor gönder
            kalan = await self.db.get_pending_count()
            rapor = f"✅ Ekleme tamamlandı ({datetime.now().strftime('%H:%M')})\n"
            rapor += f"{len(added_users)} kişi eklendi, {errors} hata\n"
            rapor += f"📦 Kalan: {kalan} kişi"
            await self.send_report(rapor)
            
            print(f"\n📊 Toplam: {len(added_users)} eklendi, {errors} hata")
            return rapor
            
        finally:
            self.is_running = False
        
    async def send_report(self, message):
        """Rapor gönder"""
        try:
            entity = await self.client.get_entity(RAPOR_HESAP)
            await self.client.send_message(entity, message)
            print("📤 Rapor gönderildi")
        except Exception as e:
            print(f"Rapor gönderilemedi: {e}")

# ==================== DM KOMUT SİSTEMİ ====================

class CommandListener:
    def __init__(self, client, db):
        self.client = client
        self.db = db
        self.adder = GroupAdder(client, db)
        self.scanner = GroupUserScanner(client, db)
        self.authorized_user = YETKILI_HESAP
        
    async def listen(self):
        """Komut dinlemeyi başlat"""
        print(f"👂 Komut dinleyici başlatıldı...")
        print(f"   🔐 Yetkili hesap: @{self.authorized_user}")
        
        @self.client.on(events.NewMessage)
        async def handler(event):
            # Sadece özel mesajları kontrol et
            if not event.is_private:
                return
            
            sender = await event.get_sender()
            sender_username = sender.username
            
            # Yetki kontrolü
            if sender_username != self.authorized_user:
                print(f"⚠️ Yetkisiz erişim denemesi: @{sender_username}")
                await event.reply("❌ Bu işlem için yetkiniz yok.")
                return
            
            # Komutu işle
            message = event.raw_text.lower().strip()
            print(f"✅ Yetkili komut: @{sender_username}: {message}")
            
            response = await self.process_command(message)
            await event.reply(response)
    
    async def process_command(self, command):
        """Komutları işle"""
        
        # yardım
        if command in ['yardim', 'yardım', 'help', 'h']:
            return """
🤖 **KOMUTLAR**

📌 **Temel Komutlar**
/ekle - Hemen ekleme başlat (günlük limit kadar)
/ekle 10 - Belirtilen sayıda ekleme yapar (max 40)
/tara - Hemen keşif başlatır
/durum - Bekleyen kullanıcı sayısını gösterir
/limit - Bugünkü ekleme durumunu gösterir

📌 **Diğer**
/iptal - Devam eden ekleme işlemini iptal eder
/yardim - Bu mesajı gösterir

💡 **Not:** Sadece sizin mesajlarınız işleme alınır.
"""
        
        # durum
        if command in ['durum', 'status']:
            pending = await self.db.get_pending_count()
            total = await self.db.get_total_users()
            return f"📊 **DURUM**\nBekleyen: {pending} kişi\nToplam: {total} kişi"
        
        # limit
        if command in ['limit', 'bugun']:
            today_added = await self.db.get_today_added_count()
            remaining = GUNLUK_LIMIT - today_added
            return f"📈 **GÜNLÜK LİMİT**\nEklenen: {today_added}/{GUNLUK_LIMIT}\nKalan: {remaining} kişi"
        
        # tara
        if command in ['tara', 'scan', 'kesif']:
            asyncio.create_task(self.run_exploration())
            return "🔍 **Keşif başlatıldı!** Sonuçlar tamamlandığında bildirilecek."
        
        # ekle
        if command.startswith('ekle'):
            parts = command.split()
            count = None
            if len(parts) > 1 and parts[1].isdigit():
                count = min(int(parts[1]), GUNLUK_LIMIT)
            
            if self.adder.is_running:
                return "⚠️ Zaten bir ekleme işlemi devam ediyor!"
            
            asyncio.create_task(self.run_adder(count))
            if count:
                return f"➕ {count} kişilik ekleme başlatıldı. Sonuçlar bildirilecek..."
            return f"➕ Ekleme başlatıldı (günlük limit: {GUNLUK_LIMIT}). Sonuçlar bildirilecek..."
        
        # iptal
        if command in ['iptal', 'cancel', 'dur']:
            if self.adder.is_running:
                self.adder.is_running = False
                return "⏹️ Ekleme işlemi iptal edildi."
            return "ℹ️ Devam eden ekleme işlemi yok."
        
        return f"❓ Bilinmeyen komut: '{command}'\n'yardim' yazarak komutları görebilirsin."
    
    async def run_exploration(self):
        """Keşif çalıştır"""
        await self.scanner.scan_all_groups()
        await self.client.send_message(YETKILI_HESAP, "✅ Keşif tamamlandı!")
    
    async def run_adder(self, custom_limit=None):
        """Ekleme çalıştır"""
        result = await self.adder.run_adder(custom_limit)
        await self.client.send_message(YETKILI_HESAP, result)

# ==================== ZAMANLAYICI VE ANA DÖNGÜ ====================

async def run_scheduled_exploration(client, db):
    """Zamanlanmış keşif çalıştır"""
    scanner = GroupUserScanner(client, db)
    await scanner.scan_all_groups()

async def run_scheduled_adder(client, db):
    """Zamanlanmış ekleme çalıştır"""
    adder = GroupAdder(client, db)
    await adder.run_adder()

async def first_time_setup(client, db, target_entity):
    """İlk çalıştırmada hedef grup üyelerini işaretle"""
    setup_file = 'setup_done.json'
    
    if not os.path.exists(setup_file):
        print("\n🔄 İLK KURULUM: Hedef grup üyeleri taranıyor...")
        count = await db.load_target_group_members(client, target_entity)
        with open(setup_file, 'w') as f:
            json.dump({'done': True, 'date': datetime.now().isoformat(), 'member_count': count}, f)
        print(f"✅ İlk kurulum tamamlandı. {count} üye işlendi.")
        await client.send_message(RAPOR_HESAP, f"🎉 İlk kurulum tamamlandı!\n{count} mevcut üye sisteme kaydedildi.")

async def main_loop():
    """Ana döngü"""
    # Veritabanını başlat
    db = Database()
    await db.init()
    
    async with TelegramClient(SESSION_NAME, API_ID, API_HASH) as client:
        await client.start(phone=PHONE_NUMBER)
        
        print("=" * 50)
        print("🤖 TELEGRAM OTOMATİK BOT BAŞLADI")
        print(f"   Hedef grup: {HEDEF_GRUP}")
        print(f"   Yetkili hesap: @{YETKILI_HESAP}")
        print(f"   Keşif saatleri: {KESIF_SAATLERI}:00")
        print(f"   Ekleme penceresi: {EKLEME_PENCERESI[0]:02d}:00 - {EKLEME_PENCERESI[1]:02d}:00")
        print(f"   Günlük limit: {GUNLUK_LIMIT}")
        print("=" * 50)
        
        # İlk kurulum kontrolü
        target_entity = await client.get_entity(HEDEF_GRUP)
        await first_time_setup(client, db, target_entity)
        
        # DM komut dinleyiciyi başlat
        cmd_listener = CommandListener(client, db)
        asyncio.create_task(cmd_listener.listen())
        
        last_kesif_date = None
        last_ekleme_date = None
        
        while True:
            try:
                now = datetime.now()
                current_hour = now.hour
                current_minute = now.minute
                current_date = now.date()
                
                # Keşif kontrolü (saat bazlı)
                if current_hour in KESIF_SAATLERI and current_minute == 0:
                    if last_kesif_date != current_date:
                        last_kesif_date = current_date
                        print(f"\n⏰ Zamanlanmış keşif: {now.strftime('%Y-%m-%d %H:%M:%S')}")
                        await run_scheduled_exploration(client, db)
                        await asyncio.sleep(60)
                
                # Ekleme kontrolü (00:00-00:30 arası)
                start_minute = EKLEME_PENCERESI[0] * 60
                end_minute = EKLEME_PENCERESI[1]
                current_minute_total = current_hour * 60 + current_minute
                
                if start_minute <= current_minute_total <= end_minute:
                    if last_ekleme_date != current_date:
                        last_ekleme_date = current_date
                        print(f"\n⏰ Zamanlanmış ekleme: {now.strftime('%Y-%m-%d %H:%M:%S')}")
                        await run_scheduled_adder(client, db)
                
            except Exception as e:
                print(f"🔄 Döngü hatası: {e}")
            
            await asyncio.sleep(30)

# ==================== BAŞLAT ====================

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        print("\n⏹️ Kullanıcı tarafından durduruldu.")
    except Exception as e:
        print(f"💥 Beklenmeyen hata: {e}")
