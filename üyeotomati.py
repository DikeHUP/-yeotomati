#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TELEGRAM OTOMATİK KULLANICI TOPLAYICI VE EKLEYİCİ
Tek script - 3 modül birleştirilmiş

Çalışma saatleri:
- 12:00 - Keşif
- 18:00 - Keşif  
- 23:00 - Keşif
- 00:00-00:30 - Ekleme (sadece bu pencerede)
"""

import asyncio
import os
import csv
import json
import zipfile
from datetime import datetime, time
import pandas as pd
from telethon import TelegramClient
from telethon.errors import FloodWaitError, UserPrivacyRestrictedError, UserIdInvalidError
from telethon.tl.functions.messages import GetHistoryRequest
from telethon.tl.functions.channels import InviteToChannelRequest, EditAdminRequest
from telethon.tl.types import ChannelParticipantAdmin, ChannelParticipantCreator, ChatAdminRights

# ==================== KONFIGÜRASYON ====================
# !!! BU ALANI KENDİ BİLGİLERİNLE DOLDUR !!!
API_ID = 33345764  # API ID'nizi girin (my.telegram.org)
API_HASH = '8576bb618f0b33ea7b15c1c249d19e28'  # API Hash'inizi girin
PHONE_NUMBER = '+99361023990'  # Telefon numaranız (+90555...)

# Hedef ve rapor grupları
HEDEF_GRUP = '@c2redcorner'  # Üye eklenecek grup (keşiften otomatik atlanır)
RAPOR_HESAP = 'merhababendike'  # Rapor gönderilecek hesap

# Zamanlama ayarları
KESIF_SAATLERI = [12, 18, 23]  # 12:00, 18:00, 23:00
EKLEME_SAATI = 0  # 00:00
EKLEME_SURESI_DAKIKA = 30  # 30 dakikalık pencere
GUNLUK_LIMIT = 40  # Günde eklenecek max kişi
BEKLEME_SANİYE = 3  # Ekleme işlemleri arası bekleme

# Dizinler
RECORDS_DIR = 'user_records'  # Keşif verileri
WORK_DIR = 'birlestirici_data'  # Birleştirilmiş veriler
MASTER_PREFIX = 'm_'  # Master dosya ön eki
SON_EKLEME_DOSYASI = 'son_ekleme_zamani.json'
SESSION_NAME = 'telegram_otomatik'  # Tek session

# ==================== YARDIMCI FONKSİYONLAR ====================

def init_dirs():
    """Gerekli dizinleri oluşturur"""
    for dir_name in [RECORDS_DIR, WORK_DIR]:
        if not os.path.exists(dir_name):
            os.makedirs(dir_name)

def sanitize_folder_name(name):
    """Dosya sistemi için güvenli klasör adı"""
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        name = name.replace(char, '_')
    return name[:100] if len(name) > 100 else name

def format_last_seen(status):
    """Son görülme bilgisini formatlar"""
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

def gunluk_limit_kontrol():
    """Günlük limit aşılmış mı kontrol eder"""
    if not os.path.exists(SON_EKLEME_DOSYASI):
        return True, 0
    
    with open(SON_EKLEME_DOSYASI, 'r') as f:
        data = json.load(f)
    
    son_tarih = datetime.fromisoformat(data['tarih'])
    eklenen_sayi = data.get('eklenen_sayi', 0)
    
    if son_tarih.date() == datetime.now().date():
        return eklenen_sayi < GUNLUK_LIMIT, eklenen_sayi
    return True, 0

def gunluk_limit_guncelle(eklenen_adet):
    """Günlük limit verisini günceller"""
    data = {
        'tarih': datetime.now().isoformat(),
        'eklenen_sayi': eklenen_adet
    }
    with open(SON_EKLEME_DOSYASI, 'w') as f:
        json.dump(data, f)

def kalan_kullanici_sayisi(dosya_yolu):
    """Master Excel'de eklenmemiş kullanıcı sayısı"""
    try:
        df = pd.read_excel(dosya_yolu)
        if 'Eklendi' not in df.columns:
            return 0
        eklendi_bool = df['Eklendi'].astype(bool)
        return len(df[~eklendi_bool])
    except:
        return 0

# ==================== 1. KEŞİF MODÜLÜ ====================

class GroupUserScanner:
    def __init__(self, client):
        self.client = client
        self.recorded_users = {}
        self.admin_cache = {}
        
    def load_records(self):
        """Daha önce kaydedilmiş kullanıcıları yükler"""
        if not os.path.exists(RECORDS_DIR):
            os.makedirs(RECORDS_DIR)
            
        for group_folder in os.listdir(RECORDS_DIR):
            group_path = os.path.join(RECORDS_DIR, group_folder)
            if os.path.isdir(group_path):
                record_file = os.path.join(group_path, 'recorded_users.txt')
                if os.path.exists(record_file):
                    with open(record_file, 'r', encoding='utf-8') as f:
                        user_ids = set(line.strip() for line in f)
                        self.recorded_users[group_folder] = user_ids
                else:
                    self.recorded_users[group_folder] = set()
                    
    def save_user_record(self, group_name, group_folder, user_id, access_hash, username, name, last_seen, scan_date):
        """Kullanıcı bilgisini access_hash ile birlikte kaydeder"""
        group_path = os.path.join(RECORDS_DIR, group_folder)
        if not os.path.exists(group_path):
            os.makedirs(group_path)
            
        if group_folder not in self.recorded_users:
            self.recorded_users[group_folder] = set()
            
        if str(user_id) in self.recorded_users[group_folder]:
            return False
            
        csv_file = os.path.join(group_path, 'members.csv')
        file_exists = os.path.exists(csv_file)
        
        with open(csv_file, 'a', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(['User ID', 'Access Hash', 'Username', 'Name', 'Son Görülme', 'İlk Görülme', 'Grup Adı'])
            
            writer.writerow([
                user_id,
                access_hash if access_hash else '',
                username if username else 'yok',
                name if name else '-',
                last_seen,
                scan_date,
                group_name
            ])
            
        self.recorded_users[group_folder].add(str(user_id))
        
        record_file = os.path.join(group_path, 'recorded_users.txt')
        with open(record_file, 'a', encoding='utf-8') as f:
            f.write(f"{user_id}\n")
            
        return True
        
    async def is_user_admin(self, group, user_id):
        """Admin kontrolü"""
        cache_key = f"{group.id}_{user_id}"
        if cache_key in self.admin_cache:
            return self.admin_cache[cache_key]
        
        try:
            async for participant in self.client.iter_participants(group):
                if participant.id == user_id:
                    if hasattr(participant, 'participant'):
                        if isinstance(participant.participant, (ChannelParticipantAdmin, ChannelParticipantCreator)):
                            self.admin_cache[cache_key] = True
                            return True
                    break
        except:
            pass
        
        self.admin_cache[cache_key] = False
        return False
        
    async def scan_group_messages(self, group):
        """Grup mesajlarını tarar ve kullanıcıları kaydeder"""
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
            
            new_users = []
            scan_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            group_folder = sanitize_folder_name(group.title)
            
            for user_id in users_found:
                try:
                    # Admin mi?
                    if await self.is_user_admin(group, user_id):
                        continue
                    
                    user = await self.client.get_entity(user_id)
                    
                    # Bot mu?
                    if hasattr(user, 'bot') and user.bot:
                        continue
                    
                    # Hedef grup mu? (Atla)
                    if HEDEF_GRUP and str(group.id) == str(HEDEF_GRUP):
                        continue
                    
                    user_id_str = str(user.id)
                    access_hash = getattr(user, 'access_hash', 0)
                    username = getattr(user, 'username', None)
                    name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
                    last_seen = format_last_seen(getattr(user, 'status', None))
                    
                    if self.save_user_record(group.title, group_folder, user_id_str, access_hash, username, name, last_seen, scan_date):
                        new_users.append({
                            'user_id': user_id_str,
                            'username': username or 'yok',
                            'name': name or '-'
                        })
                        print(f"     ✅ Yeni: {name}")
                        
                except FloodWaitError as e:
                    print(f"     ⚠️ FloodWait: {e.seconds} saniye")
                    await asyncio.sleep(e.seconds)
                except Exception:
                    continue
                    
            print(f"     📊 {len(new_users)} yeni kullanıcı")
            return group_folder, new_users
            
        except FloodWaitError as e:
            print(f"  🚫 FloodWait: {e.seconds} saniye")
            await asyncio.sleep(e.seconds)
            return None, []
        except Exception as e:
            print(f"  ❌ Hata: {type(e).__name__}")
            return None, []
            
    async def scan_all_groups(self):
        """Tüm grupları tarar (hedef grup hariç)"""
        start_time = datetime.now()
        print(f"\n🔍 Tarama başladı: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        try:
            dialogs = await self.client.get_dialogs()
            groups = [d for d in dialogs if (d.is_group or d.is_channel) and d.name != HEDEF_GRUP]
            print(f"📡 {len(groups)} grup/kanal bulundu (hedef grup atlandı)")
            
            total_new = 0
            group_results = []
            
            for i, group in enumerate(groups, 1):
                print(f"\n[{i}/{len(groups)}]", end=" ")
                group_folder, new_users = await self.scan_group_messages(group.entity)
                if new_users:
                    total_new += len(new_users)
                    group_results.append((group_folder, len(new_users)))
                await asyncio.sleep(2)
            
            # Rapor gönder (sade görünüm)
            if total_new > 0:
                rapor = f"📊 {start_time.strftime('%H:%M')} taraması tamamlandı\n"
                rapor += f"{len(groups)} grupta {total_new} yeni kullanıcı bulundu"
                await self.send_simple_report(rapor)
            else:
                await self.send_simple_report(f"📊 {start_time.strftime('%H:%M')} taraması tamamlandı\nYeni kullanıcı bulunamadı")
            
            print(f"\n✅ Tarama tamamlandı: {total_new} yeni kullanıcı")
            return total_new
            
        except Exception as e:
            print(f"🔥 Hata: {type(e).__name__} - {e}")
            return 0
            
    async def send_simple_report(self, message):
        """Basit rapor gönderir"""
        try:
            entity = await self.client.get_entity(RAPOR_HESAP)
            await self.client.send_message(entity, message)
            print("📤 Rapor gönderildi")
        except Exception as e:
            print(f"Rapor gönderilemedi: {e}")

# ==================== 2. BİRLEŞTİRİCİ MODÜLÜ ====================

def process_zip_files():
    """Keşif klasöründeki CSV'leri birleştirip master Excel oluşturur"""
    print("\n🔄 Birleştirici çalışıyor...")
    
    for group_folder in os.listdir(RECORDS_DIR):
        group_path = os.path.join(RECORDS_DIR, group_folder)
        if not os.path.isdir(group_path):
            continue
        
        csv_file = os.path.join(group_path, 'members.csv')
        if not os.path.exists(csv_file):
            continue
        
        master_file = os.path.join(WORK_DIR, f"{MASTER_PREFIX}{group_folder}.xlsx")
        
        try:
            # CSV'den verileri oku
            df = pd.read_csv(csv_file, encoding='utf-8-sig')
            
            # Gerekli sütunları kontrol et
            if 'User ID' not in df.columns:
                continue
            
            # Access Hash sütununu bul
            hash_col = None
            for col in df.columns:
                if 'access' in col.lower() and 'hash' in col.lower():
                    hash_col = col
                    break
                elif col.lower() == 'access hash':
                    hash_col = col
                    break
            
            # Master Excel'i oku veya oluştur
            if os.path.exists(master_file):
                master_df = pd.read_excel(master_file)
                # Yeni ID'leri ekle
                for _, row in df.iterrows():
                    user_id = row['User ID']
                    if user_id not in master_df['ID'].values:
                        new_row = {
                            'ID': user_id,
                            'Access Hash': row[hash_col] if hash_col else '',
                            'Eklendi': False
                        }
                        master_df = pd.concat([master_df, pd.DataFrame([new_row])], ignore_index=True)
            else:
                # Yeni master oluştur
                master_df = pd.DataFrame({
                    'ID': df['User ID'],
                    'Access Hash': df[hash_col] if hash_col else [''] * len(df),
                    'Eklendi': False
                })
            
            # Kaydet
            master_df = master_df.drop_duplicates(subset=['ID'])
            master_df.to_excel(master_file, index=False)
            
            kalan = kalan_kullanici_sayisi(master_file)
            print(f"  ✅ {group_folder}: {kalan} kullanıcı kaldı")
            
        except Exception as e:
            print(f"  ❌ {group_folder} birleştirme hatası: {e}")
    
    print("✅ Birleştirici tamamlandı")

# ==================== 3. EKLEYİCİ MODÜLÜ ====================

class GroupAdder:
    def __init__(self, client):
        self.client = client
        
    def get_master_files(self):
        """Master Excel dosyalarının listesini döndürür"""
        files = []
        if not os.path.exists(WORK_DIR):
            return files
        
        for dosya in os.listdir(WORK_DIR):
            if dosya.startswith(MASTER_PREFIX) and dosya.endswith('.xlsx'):
                files.append(os.path.join(WORK_DIR, dosya))
        return files
    
    def get_users_to_add(self, file_path, limit):
        """Excel'den eklenmemiş kullanıcıları alır (ID + Hash)"""
        try:
            df = pd.read_excel(file_path)
            
            if 'ID' not in df.columns or 'Eklendi' not in df.columns:
                return []
            
            # Eklendi sütununu bool'a çevir
            eklendi_bool = df['Eklendi'].astype(bool)
            eklenecek_df = df[~eklendi_bool].head(limit)
            
            # Hash sütununu bul
            hash_col = None
            for col in df.columns:
                if 'access' in col.lower() and 'hash' in col.lower():
                    hash_col = col
                    break
                elif col.lower() == 'hash':
                    hash_col = col
                    break
            
            users = []
            for _, row in eklenecek_df.iterrows():
                user_id = row['ID']
                hash_val = row[hash_col] if hash_col and pd.notna(row[hash_col]) else 0
                users.append((int(user_id), int(hash_val) if hash_val else 0))
            
            return users
        except Exception as e:
            print(f"  Excel okuma hatası: {e}")
            return []
    
    def mark_as_added(self, file_path, user_ids):
        """Kullanıcıları eklendi olarak işaretler"""
        try:
            df = pd.read_excel(file_path)
            for user_id, _ in user_ids:
                df.loc[df['ID'] == user_id, 'Eklendi'] = True
            df.to_excel(file_path, index=False)
            return True
        except Exception as e:
            print(f"  Excel güncelleme hatası: {e}")
            return False
    
    async def add_users_to_group(self, group_entity, users):
        """Kullanıcıları gruba ekler (gizli admin modunda)"""
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
                success_list.append((user_id, hash_val))
                
            except FloodWaitError as e:
                print(f"  ⏳ FloodWait: {e.seconds} saniye")
                await asyncio.sleep(e.seconds)
                try:
                    input_user = InputUser(user_id=user_id, access_hash=hash_val)
                    await self.client(InviteToChannelRequest(channel=group_entity, users=[input_user]))
                    success_list.append((user_id, hash_val))
                except:
                    error_count += 1
                    
            except UserPrivacyRestrictedError:
                print(f"  ✗ {user_id} gizlilik engeli")
                error_count += 1
                
            except UserIdInvalidError:
                print(f"  ✗ {user_id} geçersiz ID")
                error_count += 1
                
            except Exception as e:
                print(f"  ✗ {user_id} hata: {str(e)[:50]}")
                error_count += 1
            
            await asyncio.sleep(BEKLEME_SANİYE)
        
        return success_list, error_count
    
    async def demote_self(self, group_entity):
        """Kendini yetkisiz hale getirir (adminliği kaldırır)"""
        try:
            me = await self.client.get_me()
            # Tüm yetkileri False gönder (adminliği kaldır)
            await self.client(EditAdminRequest(
                channel=group_entity,
                user_id=me.id,
                admin_rights=ChatAdminRights(),  # Boş haklar = tüm yetkiler kalkar
                rank=""
            ))
            print("  🔒 Admin yetkisi kaldırıldı (normal üye)")
            return True
        except Exception as e:
            print(f"  ⚠️ Yetki kaldırma hatası: {e}")
            return False
    
    async def run_adder(self):
        """Ana ekleme fonksiyonu (00:00-00:30 arası çalışır)"""
        print(f"\n🌟 EKLEYİCİ BAŞLADI - {datetime.now().strftime('%H:%M:%S')}")
        
        # Günlük limit kontrolü
        limit_available, eklenen_bugun = gunluk_limit_kontrol()
        if not limit_available:
            print(f"⚠️ Günlük limit aşıldı ({GUNLUK_LIMIT}/{GUNLUK_LIMIT})")
            await self.send_report(f"⚠️ Günlük {GUNLUK_LIMIT} kişi limiti doldu. Yarın devam.")
            return
        
        kalan_kota = GUNLUK_LIMIT - eklenen_bugun
        print(f"📊 Bugün eklenen: {eklenen_bugun}, Kalan kota: {kalan_kota}")
        
        # Hedef grubu al
        try:
            group_entity = await self.client.get_entity(HEDEF_GRUP)
            print(f"🎯 Hedef grup: {group_entity.title}")
        except Exception as e:
            print(f"❌ Hedef grup bulunamadı: {e}")
            await self.send_report(f"❌ Hedef grup bulunamadı: {HEDEF_GRUP}")
            return
        
        # Master dosyaları al
        master_files = self.get_master_files()
        if not master_files:
            print("❌ Hiç master listesi bulunamadı!")
            return
        
        total_added = 0
        total_errors = 0
        
        for file_path in master_files:
            if total_added >= kalan_kota:
                break
            
            remaining = kalan_kota - total_added
            users = self.get_users_to_add(file_path, remaining)
            
            if not users:
                continue
            
            print(f"\n📋 {os.path.basename(file_path)}: {len(users)} kişi eklenecek")
            
            added, errors = await self.add_users_to_group(group_entity, users)
            
            if added:
                self.mark_as_added(file_path, added)
                total_added += len(added)
            total_errors += errors
            
            if total_added >= kalan_kota:
                break
        
        # Günlük limiti güncelle
        gunluk_limit_guncelle(eklenen_bugun + total_added)
        
        # Rapor gönder
        rapor = f"✅ Ekleme tamamlandı ({datetime.now().strftime('%H:%M')})\n"
        rapor += f"{total_added} kişi eklendi, {total_errors} hata\n"
        kalan_toplam = sum([kalan_kullanici_sayisi(f) for f in master_files])
        rapor += f"Kalan: {kalan_toplam} kişi"
        await self.send_report(rapor)
        
        print(f"\n📊 Toplam: {total_added} eklendi, {total_errors} hata")
        
        # Kendini yetkisiz yap (adminliği kaldır)
        await self.demote_self(group_entity)
        
    async def send_report(self, message):
        """Rapor gönderir"""
        try:
            entity = await self.client.get_entity(RAPOR_HESAP)
            await self.client.send_message(entity, message)
            print("📤 Rapor gönderildi")
        except Exception as e:
            print(f"Rapor gönderilemedi: {e}")

# ==================== 4. ZAMANLAYICI VE ANA DÖNGÜ ====================

async def run_exploration(client):
    """Keşif çalıştır"""
    scanner = GroupUserScanner(client)
    scanner.load_records()
    await scanner.scan_all_groups()
    # Keşif biter bitmez birleştiriciyi çalıştır
    process_zip_files()

async def run_adder_job(client):
    """Ekleme işini çalıştır (sadece belirli saat aralığında)"""
    now = datetime.now()
    current_time = now.time()
    
    # Ekleme penceresi: 00:00 - 00:30
    start_time = time(0, 0)
    end_time = time(0, EKLEME_SURESI_DAKIKA)
    
    if start_time <= current_time <= end_time:
        adder = GroupAdder(client)
        await adder.run_adder()
        return True
    return False

async def main_loop():
    """Ana döngü - Sürekli çalışır ve zamanlanmış görevleri yürütür"""
    init_dirs()
    
    async with TelegramClient(SESSION_NAME, API_ID, API_HASH) as client:
        await client.start(phone=PHONE_NUMBER)
        print("=" * 50)
        print("🤖 TELEGRAM OTOMATİK BOT BAŞLADI")
        print(f"   Hedef grup: {HEDEF_GRUP}")
        print(f"   Keşif saatleri: {KESIF_SAATLERI}")
        print(f"   Ekleme penceresi: 00:00-00:{EKLEME_SURESI_DAKIKA}")
        print(f"   Günlük limit: {GUNLUK_LIMIT}")
        print("=" * 50)
        
        # İlk çalıştırmada hedef grubun mevcut üyelerini "eklendi" olarak işaretle
        first_run_file = 'first_run_done.json'
        if not os.path.exists(first_run_file):
            print("\n🔄 İLK ÇALIŞTIRMA: Hedef grup üyeleri işaretleniyor...")
            try:
                group_entity = await client.get_entity(HEDEF_GRUP)
                adder = GroupAdder(client)
                
                # Hedef grubun mevcut üyelerini al
                members_file = os.path.join(WORK_DIR, f"{MASTER_PREFIX}hedef_grup_mevcut.xlsx")
                member_ids = []
                
                async for member in client.iter_participants(group_entity):
                    if not member.bot:
                        member_ids.append({
                            'ID': member.id,
                            'Access Hash': getattr(member, 'access_hash', 0),
                            'Eklendi': True
                        })
                
                if member_ids:
                    df = pd.DataFrame(member_ids)
                    df.to_excel(members_file, index=False)
                    print(f"✅ Hedef gruptaki {len(member_ids)} üye 'eklendi' olarak işaretlendi")
                
                with open(first_run_file, 'w') as f:
                    json.dump({'done': True, 'date': datetime.now().isoformat()}, f)
            except Exception as e:
                print(f"⚠️ İlk çalıştırma hatası: {e}")
        
        # Ana döngü - her dakika kontrol et
        last_daily_report = None
        
        while True:
            try:
                now = datetime.now()
                current_hour = now.hour
                current_minute = now.minute
                current_date = now.date()
                
                # Keşif kontrolü (saat bazlı)
                if current_minute == 0 and current_hour in KESIF_SAATLERI:
                    if last_daily_report != f"{current_date}_{current_hour}_kesif":
                        last_daily_report = f"{current_date}_{current_hour}_kesif"
                        print(f"\n⏰ Zamanlanmış keşif: {now.strftime('%Y-%m-%d %H:%M:%S')}")
                        await run_exploration(client)
                        await asyncio.sleep(60)  # 1 dakika bekle, aynı saatte tekrar çalışmasın
                
                # Ekleme kontrolü (00:00-00:30 arası)
                if current_hour == 0 and current_minute < EKLEME_SURESI_DAKIKA:
                    if last_daily_report != f"{current_date}_ekleme":
                        last_daily_report = f"{current_date}_ekleme"
                        await run_adder_job(client)
                
            except Exception as e:
                print(f"🔄 Döngü hatası: {e}")
            
            await asyncio.sleep(30)  # Her 30 saniyede bir kontrol et

# ==================== BAŞLAT ====================

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        print("\n⏹️ Kullanıcı tarafından durduruldu.")
    except Exception as e:
        print(f"💥 Beklenmeyen hata: {e}")