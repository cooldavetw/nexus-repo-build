# nexus-bundle

建立可攜式壓縮封裝，包含 Nexus、Python wheel 套件及相依套件、Docker Compose 設定，以及離線部署與上傳腳本。
CLI 使用 Python 標準函式庫；部署工具另行封裝在 Docker 映像檔中。

## 快速開始

建置主機需要 Linux、Python 3.10 以上版本、Docker Engine、Docker Compose，以及網際網路連線。
可直接執行，無須先安裝 CLI：

```bash
python3 -m nexus_bundle init
cp requirements.txt.example requirements.txt
# 編輯 requirements.txt 與 bundle.json。
python3 -m nexus_bundle build --config bundle.json
python3 -m nexus_bundle verify dist/nexus-bundle.tar.gz
```

也可執行 `python3 -m pip install .` 安裝，再使用 `nexus-bundle` 指令。
將產生的壓縮檔複製到離線主機後，執行：

```bash
tar -xzf nexus-bundle.tar.gz
cd nexus-bundle
./verify-bundle.sh
./start.sh
./bootstrap-nexus.sh
./upload-wheels.sh
./verify-install.sh
```

## 設定

`init` 會產生 JSON 設定檔，且不會覆寫既有檔案。
所有相對路徑皆以設定檔所在目錄為基準。

| 設定名稱 | 用途 |
| --- | --- |
| output | tar.gz 輸出路徑；不會覆寫既有輸出檔案 |
| nexus_image | 指定固定標籤或摘要（digest）的 Nexus 映像檔 |
| container_platform | linux/amd64 或 linux/arm64 |
| python_image | 指定固定版本的 Python 映像檔，作為套件收集與驗證環境 |
| repository | PyPI 託管儲存庫（hosted repository）名稱 |
| requirements | 輸入的套件需求檔案 |

預設環境為 Debian Linux amd64 上的 CPython 3.12。
wheel 收集作業會在指定的 Python 映像檔與處理器架構中執行。
跨架構建置需要 Docker 模擬功能，建議優先使用相同架構的主機建置。
請選擇與應用程式相容的環境，並同時考量 Nexus 主機的需求。
目前每個封裝支援一組 Python／作業系統／CPU 目標環境。
僅收集二進位 wheel；若找不到相容的 wheel，建置會失敗。
含原生程式碼的 wheel 也可能需要應用程式映像檔中尚未提供的系統函式庫。

輸入格式為每行一個固定版本的 `package==version`，可加上額外功能（extras）。
支援空白行與註解。不接受引用其他需求檔案、URL、本機路徑、環境標記（markers）或 pip 選項，
以避免離線驗證時連線至其他套件來源。
收集時會解析相依套件，並將所有解析後的版本記錄於 `requirements.txt`；
原始版本設定則保留在 `requirements.input.txt`。
若需讓不同時間的建置結果保持一致，也請在輸入檔案中固定間接相依套件的版本。

建置流程會拉取 Nexus 與 Python 映像檔，建立含固定版本 pip／Twine 的工具映像檔，
下載 wheel，儲存兩個部署映像檔，記錄映像檔 ID／摘要與套件雜湊值，
並在驗證成功後，以原子操作產生最終壓縮檔。
上游可能重新發布相同映像檔標籤；若需固定映像檔內容，請使用摘要參照。
建置中斷時不會留下部分完成的最終壓縮檔，但可能保留已建立的 Docker 映像檔。

## 部署行為

Nexus 預設僅綁定 localhost，並使用持續保存資料的具名磁碟區。
初始化腳本會透過 REST API 變更初始管理員密碼、啟用匿名讀取，
並建立禁止重新部署相同版本的 PyPI 託管儲存庫。
密碼可在提示時輸入，或透過 `NEXUS_PASSWORD` 提供；不會寫入封裝。
首次部署時，初始化腳本會在 Nexus 容器內讀取初始密碼，且不會將其顯示出來。

上傳使用 Twine。再次執行時，會先比對遠端 SHA-256 校驗值，必要時使用 SHA-1，
再略過已存在且內容相同的套件；若內容不同，則回報失敗。
`verify-install.sh` 會建立乾淨的虛擬環境，從 Nexus 安裝全部已解析的套件，
再執行 `pip check`。部署主機不需要安裝 Python。
網路開放方式與資料保存細節請參閱封裝內的 README。

可編輯的 `.env` 不納入校驗；`.env.example` 則記錄預設設定。
校驗值可偵測檔案損毀，但無法防止惡意竄改，請透過可信任的管道傳送封裝。
此封裝用於部署，不包含既有 Nexus 資料的備份。

## 開發與驗證

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q nexus_bundle
# 閱讀 Sonatype CE EULA 後，在無法存取外部網路的環境中執行測試：
python3 tests/integration.py dist/nexus-bundle.tar.gz --accept-nexus-eula
```

參考文件：[Nexus PyPI](https://help.sonatype.com/en/configure-pypi-with-nexus.html)、
[Nexus API](https://help.sonatype.com/en/api-reference.html)、
[Docker save](https://docs.docker.com/reference/cli/docker/image/save/)、
[pip download](https://pip.pypa.io/en/stable/cli/pip_download/)。

Nexus Community Edition 必須先接受終端使用者授權合約（EULA），才能上傳或下載套件。
初始化腳本會顯示伺服器提供的聲明，並要求明確同意。
若需無人值守部署，請先閱讀 [Sonatype CE EULA](https://links.sonatype.com/products/nxrm/ce-eula)，
再執行 `./bootstrap-nexus.sh --accept-eula` 授權接受合約。
整合測試同樣需要 `--accept-nexus-eula`；建立封裝時不會自動接受任何 EULA。

## 更新既有 Nexus 的套件

```bash
# 小型封裝：重用目標主機上已載入的上傳工具映像檔。
python3 -m nexus_bundle build-wheels --config bundle.json
# 包含獨立離線上傳工具；不包含 Nexus 映像檔。
python3 -m nexus_bundle build-wheels --config bundle.json --include-uploader \
  --output dist/nexus-wheels-standalone.tar.gz
python3 -m nexus_bundle verify dist/nexus-wheels-update.tar.gz
```

更新封裝的預設輸出為 `dist/nexus-wheels-update.tar.gz`，以設定檔目錄為基準，
不受完整部署封裝的 `output` 設定影響。
可用 `--output` 指定其他檔名；不會覆寫既有輸出檔案。
套件需求與 Python 環境設定皆與完整建置共用。
建立更新封裝時不會拉取或封裝 Nexus，也不需要 Compose。

```bash
tar -xzf nexus-wheels-update.tar.gz
cd nexus-wheels-update
./upload-wheels.sh --url http://localhost:8081 --repository pypi-offline
# 也可使用執行中容器所屬的 Docker 網路：
./upload-wheels.sh --url http://nexus:8081 --network existing-compose_default
```

密碼可在提示時輸入，或透過 `NEXUS_PASSWORD` 提供。
使用非管理員帳號上傳時，可用 `--username` 指定帳號。
目標儲存庫必須事先完成設定。
更新作業會上傳收集到的全部 wheel 與相依套件、比對既有校驗值，並保留舊套件版本。
不會初始化 Nexus，也不會變更 EULA、密碼、匿名存取、儲存庫設定或資料磁碟區。

上傳腳本預設使用 Linux 主機網路。若需以容器 DNS 名稱連線，
請用 `--network` 選擇既有 Docker 網路。
HTTPS 使用標準憑證驗證。
小型更新封裝會在 `uploader-image.txt` 記錄所需的上傳工具映像檔；
可從完整封裝載入，或用 `--image` 指定相容的 Python／Twine 7 以上版本映像檔。
獨立更新封裝會自動載入內附的上傳工具映像檔。
兩種模式皆不會在目標主機拉取映像檔；詳細說明請參閱更新封裝內的 README。
