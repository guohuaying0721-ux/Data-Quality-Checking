import json, os, requests

# DS-202606-0044 华楷PLD 验证集
manifest_file = r"E:\华楷数据集\DS-202606-0044_manifest.json"
output_dir = r"E:\华楷数据集\DS-202606-0044_华楷PLD"

with open(manifest_file, 'r', encoding='utf-8') as f:
    m = json.load(f)

print(f"数据集: {m['datasetCode']} - {m['datasetName']}")
print(f"总文件数: {m['total']}")
print(f"下载目录: {output_dir}")
print("="*60)

os.makedirs(output_dir, exist_ok=True)

downloaded = 0
failed = 0
skipped = 0

for item in m['files']:
    wav_name = item['originalWav']
    json_name = wav_name.replace('.wav', '.json')
    wav_path = os.path.join(output_dir, wav_name)
    json_path = os.path.join(output_dir, json_name)

    # 下载WAV
    if not os.path.exists(wav_path):
        try:
            with requests.get(item['sourceWavUrl'], stream=True, timeout=300) as r:
                r.raise_for_status()
                with open(wav_path, 'wb') as f:
                    for chunk in r.iter_content(64*1024):
                        f.write(chunk)
            downloaded += 1
            print(f"[OK] {wav_name}")
        except Exception as e:
            failed += 1
            print(f"[FAIL] {wav_name}: {e}")
    else:
        skipped += 1
        print(f"[SKIP] {wav_name} 已存在")

    # 根据 detectPointName 确定检测工位
    # 华楷PLD: 通道1=中工位, 通道2=右工位
    detect_point = item['detectPointName']
    if detect_point == '通道1':
        detect_station_id = "493537e9d4824459a0c6e52f581e86a1"
        detect_station_name = "HKPLD-GZ-0007-中"
        detect_station_number = "01"
    elif detect_point == '通道2':
        detect_station_id = "0e8164420b3f438b95ab51718e7a5667"
        detect_station_name = "HKPLD-GZ-0007-右"
        detect_station_number = "02"
    else:
        detect_station_id = ""
        detect_station_name = ""
        detect_station_number = ""

    # 生成JSON元数据
    metadata = {
        "datasetCode": m['datasetCode'],
        "datasetName": m['datasetName'],
        "version": m['version'],
        "workstationId": m.get('workstationId'),
        "workstationName": "华楷PLD",
        "workstationCode": "HKPLD",
        "detectStationId": detect_station_id,
        "detectStationName": detect_station_name,
        "detectStationNumber": detect_station_number,
        "fileID": item['fileID'],
        "originalWav": item['originalWav'],
        "dateDir": item['dateDir'],
        "channelID": item['channelID'],
        "productUniqueCode": item['productUniqueCode'],
        "detectPointName": item['detectPointName'],
        "channel": item['channel'],
        "stage": item['stage'],
        "channelCount": item['channelCount']
    }
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

print("="*60)
print(f"下载完成: 成功 {downloaded}, 失败 {failed}, 跳过 {skipped}")
