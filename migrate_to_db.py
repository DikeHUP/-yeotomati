#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mevcut CSV/Excel dosyalarını PostgreSQL database'e aktarma script'i
"""

import os
import asyncio
import asyncpg
import pandas as pd
from glob import glob

# ==================== KONFIGÜRASYON ====================
DATABASE_URL = 'postgresql://otomat_database_user:ztcFFMeJq6AzQnndkVIoK2TmUwhI94HO@dpg-d8i3rg6q1p3s73eajeig-a/otomat_database'

RECORDS_DIR = 'user_records'  # Keşif CSV'lerinin olduğu klasör
WORK_DIR = 'birlestirici_data'  # Master Excel'lerin olduğu klasör

async def create_tables(conn):
    """Tabloları oluştur"""
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
    
    print("✅ Tablolar oluşturuldu/hazır")

async def migrate_csv_files(conn):
    """CSV dosyalarını aktar (user_records klasörü)"""
    print("\n📁 CSV dosyaları taranıyor...")
    
    csv_files = glob(f"{RECORDS_DIR}/**/members.csv", recursive=True)
    total_migrated = 0
    
    for csv_file in csv_files:
        try:
            df = pd.read_csv(csv_file, encoding='utf-8-sig')
            
            # Gerekli sütunlar var mı?
            if 'User ID' not in df.columns:
                print(f"  ⚠️ {csv_file}: 'User ID' sütunu yok, atlanıyor")
                continue
            
            # Access Hash sütununu bul
            hash_col = None
            for col in df.columns:
                if 'access' in col.lower() and 'hash' in col.lower():
                    hash_col = col
                    break
            
            # Grup adını dosya yolundan al
            group_name = os.path.basename(os.path.dirname(csv_file))
            
            count = 0
            for _, row in df.iterrows():
                user_id = row['User ID']
                
                # ID'yi integer'a çevir
                try:
                    user_id = int(user_id)
                except:
                    continue
                
                access_hash = row[hash_col] if hash_col and pd.notna(row[hash_col]) else 0
                if access_hash:
                    try:
                        access_hash = int(access_hash)
                    except:
                        access_hash = 0
                
                username = row.get('Username', '')
                name = row.get('Name', '')
                last_seen = row.get('Son Görülme', 'Bilinmiyor')
                
                await conn.execute('''
                    INSERT INTO users (id, access_hash, username, name, last_seen, group_name, discovered_at)
                    VALUES ($1, $2, $3, $4, $5, $6, CURRENT_TIMESTAMP)
                    ON CONFLICT (id) DO UPDATE SET
                        access_hash = EXCLUDED.access_hash,
                        username = EXCLUDED.username,
                        name = EXCLUDED.name,
                        last_seen = EXCLUDED.last_seen,
                        group_name = EXCLUDED.group_name
                ''', user_id, access_hash, username[:100] if username else '', 
                    name[:100] if name else '-', last_seen[:50], group_name[:100])
                count += 1
            
            total_migrated += count
            print(f"  ✅ {csv_file}: {count} kullanıcı aktarıldı")
            
        except Exception as e:
            print(f"  ❌ {csv_file} hatası: {e}")
    
    return total_migrated

async def migrate_excel_files(conn):
    """Excel dosyalarını aktar (birlestirici_data klasörü)"""
    print("\n📊 Excel dosyaları taranıyor...")
    
    excel_files = glob(f"{WORK_DIR}/m_*.xlsx")
    total_migrated = 0
    
    for excel_file in excel_files:
        try:
            df = pd.read_excel(excel_file)
            
            if 'ID' not in df.columns:
                print(f"  ⚠️ {excel_file}: 'ID' sütunu yok, atlanıyor")
                continue
            
            # Grup adını dosya adından al
            group_name = os.path.basename(excel_file).replace('m_', '').replace('.xlsx', '')
            
            # Hash sütununu bul
            hash_col = None
            for col in df.columns:
                if 'hash' in col.lower():
                    hash_col = col
                    break
            
            count = 0
            for _, row in df.iterrows():
                user_id = row['ID']
                try:
                    user_id = int(user_id)
                except:
                    continue
                
                access_hash = row[hash_col] if hash_col and pd.notna(row[hash_col]) else 0
                
                # Eklendi mi bilgisi varsa onu da işle
                is_added = False
                if 'Eklendi' in df.columns:
                    is_added = bool(row['Eklendi'])
                
                await conn.execute('''
                    INSERT INTO users (id, access_hash, group_name, is_added, discovered_at)
                    VALUES ($1, $2, $3, $4, CURRENT_TIMESTAMP)
                    ON CONFLICT (id) DO UPDATE SET
                        access_hash = EXCLUDED.access_hash,
                        group_name = EXCLUDED.group_name,
                        is_added = EXCLUDED.is_added OR users.is_added
                ''', user_id, access_hash, group_name[:100], is_added)
                count += 1
            
            total_migrated += count
            print(f"  ✅ {excel_file}: {count} kullanıcı aktarıldı (eklendi: {df['Eklendi'].sum() if 'Eklendi' in df.columns else 0})")
            
        except Exception as e:
            print(f"  ❌ {excel_file} hatası: {e}")
    
    return total_migrated

async def check_duplicates(conn):
    """Tekrar eden kayıtları kontrol et"""
    row = await conn.fetchrow('''
        SELECT COUNT(*) as total, COUNT(DISTINCT id) as unique_count 
        FROM users
    ''')
    
    duplicates = row['total'] - row['unique_count']
    
    if duplicates > 0:
        print(f"\n⚠️ {duplicates} tekrar eden kayıt bulundu (otomatik temizlenecek)")
        await conn.execute('''
            DELETE FROM users a USING users b 
            WHERE a.id = b.id AND a.ctid < b.ctid
        ''')
        print(f"✅ Tekrar eden kayıtlar temizlendi")
    
    return row['unique_count']

async def main():
    """Ana aktarma fonksiyonu"""
    print("=" * 50)
    print("🔄 VERİ AKTARMA BAŞLADI")
    print(f"   CSV klasörü: {RECORDS_DIR}")
    print(f"   Excel klasörü: {WORK_DIR}")
    print("=" * 50)
    
    try:
        # Veritabanına bağlan
        conn = await asyncpg.connect(DATABASE_URL)
        print("✅ Veritabanına bağlandı")
        
        # Tabloları oluştur
        await create_tables(conn)
        
        # CSV'leri aktar
        csv_count = await migrate_csv_files(conn)
        
        # Excel'leri aktar
        excel_count = await migrate_excel_files(conn)
        
        # Tekrarları temizle
        total_unique = await check_duplicates(conn)
        
        # Özet rapor
        print("\n" + "=" * 50)
        print("📊 AKTARMA RAPORU")
        print(f"   CSV'den aktarılan: {csv_count} kullanıcı")
        print(f"   Excel'den aktarılan: {excel_count} kullanıcı")
        print(f"   Toplam benzersiz: {total_unique} kullanıcı")
        
        # Hedef grupta olup "is_added" işaretlenmemişleri kontrol et
        print("\n📌 NOT: Hedef gruptaki mevcut üyeler için")
        print("   Ana script ilk çalıştığında otomatik 'eklendi' olarak işaretlenecek")
        
        await conn.close()
        print("\n✅ Aktarma tamamlandı!")
        
    except Exception as e:
        print(f"\n❌ HATA: {e}")

if __name__ == '__main__':
    asyncio.run(main())
