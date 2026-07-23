# 這個專案怎麼跑起來？新手版說明

先講重點：你不需要全部看懂，跟著步驟一步一步做，卡住再回來看「卡住怎麼辦」那段就好。

## 第一步：把資料夾打開

解壓縮之後，打開 VSCode，點左上角「File」→「Open Folder」，選剛剛解壓縮出來的 `hybrid-rag-project` 資料夾。

順手裝兩個 VSCode 的擴充套件（左邊工具列有個像積木的圖案，點進去搜尋安裝）：
- **Python**（Microsoft 出的，讓 VSCode 看得懂 Python）
- **Pylance**（讓 VSCode 提示錯誤更聰明）

這兩個裝好基本上就夠用了，不裝也能跑，只是比較沒有提示。

## 第二步：把「虛擬環境」建起來

先解釋一下這是什麼：你電腦裡可能已經裝了很多 Python 套件，如果全部混在一起，很容易某天裝了新東西把舊的東西弄壞。「虛擬環境」就是幫這個專案獨立開一個乾淨的小房間，裝的東西只有這個專案在用，不會互相干擾。

打開 VSCode 下方的終端機（快捷鍵是按住 Ctrl 再按那個 `` ` `` 鍵，就在數字鍵 1 左邊），貼上以下指令：

**如果你是 Mac 或 Linux：**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**如果你是 Windows：**
```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

跑完最後一行會裝一堆東西，跑久一點是正常的，等它跑完就好。

裝完之後，VSCode 右下角應該會跳出提示問你要不要選這個 Python，選「是」，或是自己點右下角切換到 `venv` 那個版本。

**怎麼確認有成功？** 終端機最前面如果出現 `(venv)` 這幾個字，就代表你現在在這個乾淨的小房間裡了，之後裝東西都不會影響到你電腦其他地方。

## 第三步：申請一組免費的 API 金鑰

這個專案要呼叫 AI 模型才能運作，所以需要一組「金鑰」（可以想成是你的專屬密碼，用來證明這個請求是你授權的）。

這裡用的是 **Google Gemini 的免費額度**，不用信用卡、不用付錢，申請完直接能用：

1. 打開 https://aistudio.google.com/apikey
2. 用你的 Google 帳號登入
3. 點「Create API key」，選一個專案（沒有的話它會幫你建一個新的）
4. 複製產生出來的那串金鑰

免費額度目前大約是一天 1000 多次請求，對你這個練習/比賽用途完全夠用。要注意兩件事：
- 免費額度的用量 Google 可能會拿去改善他們的模型，如果你的資料是機密的（比如真實客戶財報），正式上線建議改用付費版
- 免費額度是「整個專案共用」，不是每組金鑰各自獨立，所以不用一直重新申請新的金鑰

拿到金鑰後，先複製一份範例檔案：
```bash
cp .env.example .env
```
（Windows 上如果指令不能用，改打 `copy .env.example .env`）

複製完之後，在 VSCode 左邊檔案列表找到新出現的 `.env` 檔案，點開它，把 `your_key_here` 換成你剛剛複製的金鑰，貼上去就好，不要留空格。

## 第四步：準備一份簡報當測試資料

隨便找一份法說會或財報的 PDF，丟到專案最外層那層資料夾（跟 `README.md` 同一層），然後把檔名改成 `sample.pdf`。

## 第五步：一個一個功能分開測試

程式拆成好幾支小檔案，建議一支一支跑，確認每個都沒問題，之後串起來才不會不知道哪裡壞掉。

在終端機打：
```bash
cd src
```

然後照順序執行（每次跑一行，按 Enter，等它跑完再跑下一行）：

```bash
python preprocess_pdf.py
```
這支是把 PDF 每一頁拆成一張一張圖片。

```bash
python vlm_parse.py
```
這支是把圖片丟給 AI 看，讓它讀懂圖表上的數字。這支會真的呼叫 API，會花一點點費用，不用太擔心，測試用量很小。

```bash
python vector_rag.py
```
這支在測試「找相關文字」的功能，不用真實資料也能跑起來看效果。

```bash
python graph_rag.py
```
這支在測試「精準算數字」的功能，比如季增率、年增率，這部分是直接用公式算，不會讓 AI 用猜的。

```bash
python agent_router.py
```
這支把上面幾個功能串起來，測試「AI 自己判斷該用哪個方法回答問題」。

```bash
python report_generator.py
```
這支是把分析結果整理成一份 Word 文件。

**懶得打指令的話**，也可以直接在 VSCode 裡打開某支檔案，在程式碼上按右鍵，選「Run Python File」，效果一樣。

## 第六步：打開網頁介面，實際跟它聊天

有兩個介面，**平常用第一個就好**，第二個是早期版本、留著當備援。

### 主要介面：FastAPI ＋ 網頁前端（展示時用這個）

回到最外層資料夾（打 `cd ..` 回上一層），執行：

**Windows：**
```powershell
venv\Scripts\python.exe -m uvicorn api:app --app-dir src --reload --port 8000
```

**Mac / Linux：**
```bash
venv/bin/python -m uvicorn api:app --app-dir src --reload --port 8000
```

然後打開瀏覽器到 `http://localhost:8000`。四個分頁分別是：分析儀表板、跨機構比較、問答與報告、資料來源總覽。
API 文件在 `http://localhost:8000/docs`（FastAPI 自動產生的，可以直接在上面試打每個 endpoint）。

前端是手寫的 HTML/CSS/JS（放在 `web/`），沒有打包步驟，改完存檔重新整理就看得到。
如果改了畫面卻沒變化，那是瀏覽器快取，把 `web/index.html` 裡 `app.js?v=2` 的數字 +1 就會強制更新。

### 備援介面：Streamlit

```bash
streamlit run src/app.py
```
會自動開瀏覽器到 `http://localhost:8501`。功能比較陽春，但不用管前端檔案。

## 這個資料夾裡面各個檔案在幹嘛

```
hybrid-rag-project/
├── src/
│   ├── preprocess_pdf.py    → 把 PDF 拆成一張張圖片
│   ├── vlm_parse.py         → 讓 AI 讀懂圖片裡的圖表和數字
│   ├── stt_parse.py         → 把法說會錄音轉成逐字稿
│   ├── ingest.py            → 把解析結果匯入知識庫（重匯同一期會先清舊的）
│   ├── vector_rag.py        → 負責「找出相關的文字說明」
│   ├── graph_rag.py         → 結構化指標庫，負責「精準計算數字變化」（命名見下方說明）
│   ├── metric_alignment.py  → 判斷指標是比率還是金額、是不是累計值
│   ├── standard_metrics.py  → 各家用詞不同的 ROE/NIM… 對齊到同一個定義
│   ├── agent_router.py      → 負責「判斷問題該用哪個方法回答」
│   ├── eap_client.py        → 串接 EAP 平台的聊天 API
│   ├── backfill_units.py    → 維護用：把單位從解析結果回填／校正到指標庫
│   ├── report_generator.py  → 把結果整理成 Word 報告
│   ├── api.py               → FastAPI 後端（主要介面）
│   └── app.py               → Streamlit 介面（備援）
├── web/          → 手寫的前端 HTML/CSS/JS，由 api.py 直接提供
├── pages/        → PDF 拆出來的圖片會自動存在這裡
├── tests/        → 自動化測試（pytest），跑法見下方「怎麼跑測試」
├── vector_db/    → 知識庫（ChromaDB ＋ 指標庫 JSON），有進版控，見下方說明
├── outputs/      → 解析結果 JSON、逐字稿、產生的報告
├── requirements.txt        → 執行需要的套件（只寫下界）
├── requirements.lock.txt   → 鎖定版本，要重現「實測跑得起來的那一版」用這支
├── requirements-dev.txt    → 開發／測試才需要的套件（pytest…）
├── render.yaml        → Render 一鍵部署設定
├── .env.example       → 金鑰範例檔（記得複製成 .env 再填金鑰）
├── LICENSE            → MIT 授權
└── .gitignore         → 告訴 Git 哪些檔案不用管，新手可以先忽略這個
```

### 為什麼 `graph_rag.py` 裡沒有「圖」

檔名叫 graph_rag、底層用的是 networkx，但它**目前沒有任何邊**：存檔只寫 nodes，
全庫找不到一個 `add_edge`，所有查詢都是對節點做線性掃描。實際上它是一個
**結構化指標庫**——每筆「公司｜指標｜期間」是一個唯一鍵，數值掛在上面。

這是刻意的取捨，不是還沒做完。早期真的建過關係式圖譜（公司／事業體／指標／期間各自成
節點，數值掛在「申報」關係上，`export_graph_csv.py --format flat` 保留了那版格式），
但實測**跨期間會答錯**：同一個指標名稱在不同季共用同一個節點，問 2026Q1 的 EPS 會拿到
別季的值。改成唯一鍵之後才正確。財報數字的存取本來就是「精準定位單一格」而不是
「探索多跳關係」，扁平唯一鍵才是對的資料模型。

真正的關聯圖建在 **EAP 平台那一側**（`export_graph_csv.py --format precise` 匯出給平台的
New Flow 建圖）。所以對外描述請講「結構化指標庫」，「知識圖譜」留給 EAP 那一層。

## 怎麼跑測試

```bash
venv/Scripts/python.exe -m pip install -r requirements-dev.txt
venv/Scripts/python.exe -m pytest
```

測的是那些「錯了不會報錯、只會靜靜給出錯數字」的規則：單位正規化（十億不可被壓成億）、
累計值判定（累計值跨季不算 QoQ）、EAP 查無資料偵測、以及限流與上傳大小上限。

其中有一條是**掃原始碼**的：只要某個端點會呼叫外部模型，就必須也呼叫 `_rate_limit`。
`/api/summary` 就是這樣被抓到的——它是 GET、長得跟其他讀資料的端點一樣，
實際上每按一次「生成本期總結」就呼叫一次 Gemini。這種漏網之魚靠人工記不住。

測試**不會**呼叫 Gemini 或 EAP，也不會寫進 `vector_db/`——指標都是塞進記憶體、測完移除
（見 `tests/conftest.py` 的 `temp_metrics`）。所以可以放心重複跑，不花任何額度。

### 關於 `vector_db/` 為什麼進版控

一般來說資料庫不該進 Git，但這裡是刻意的：雲端部署（Streamlit Cloud / Render）只會從 repo 拉檔案，
知識庫不在裡面的話，部署出去的網站就是空的，而使用者也沒辦法自己重跑一次 VLM 解析。
整個只有 2 MB 出頭，而且這是 demo 專案不是正式系統，值得這個取捨。
**`.env`（你的金鑰）有被 `.gitignore` 擋住，不會上傳。**

## EAP 平台

EAP 已經串好了（`src/eap_client.py`，用的是官方的聊天 API）。在「問答與報告」那一頁把
「改用 EAP 平台回答」打開，問題就會送到 EAP 而不是 Gemini。要用的話得在 `.env` 補三個值：

```
EAP_API_BASE_URL=https://cloud.geminidata.com/api/portal/api10
EAP_PROJECT_ID=（專案 ID，在專案網址列找得到）
EAP_API_KEY=（後台「管理專案」→「通證管理」→「新增通證」取得）
```

EAP 的資料是在它後台網頁手動上傳的，我們的程式碰不到它的知識庫。想確認平台上到底有沒有某家公司的資料，
跑 `venv/Scripts/python.exe src/check_eap_data.py`，它會直接問平台本人（只讀不寫）。

### EAP 回答後的五道防護

EAP 是外部平台，它的數字我們無法保證正確，所以答案回來後會逐層檢查（都在
`api._finalize_eap`）。每一道都對應一種實測遇過的錯，而且**前一道攔不住的才需要下一道**：

| # | 防什麼 | 觸發條件 |
|---|---|---|
| 1 | 平台查不到資料 | 用本地 Vector RAG 檢索、再交給 EAP 生成；仍答不出來就給完全走本地的退路 |
| 2 | 數字不符 | 兩邊的指標差超過門檻就標紅，不斷言誰對誰錯 |
| 3 | **比較本身無效** | 數字都對，但拿「年初至今累計」跨季相比 |
| 4 | 講得頭頭是道但驗不了 | 一條數字或說法都比對不到時誠實標示 |
| 5 | **部分驗證** | 驗過幾筆、哪幾筆本地沒有對應資料，分開講清楚 |

第 3 道是實測補上的：EAP 回「2026Q1 EPS 1.18 元，較 2025Q4 的 4.08 元減幅約 71%」——
兩個數字各自都正確、交叉驗證也過，但 4.08 是 2025 全年累計、1.18 是新年度首季，
那個 -71% 是重新起算不是衰退。前面每一道都攔不住：有回答、數字都對、驗得過、有數字。
而本地其實早就知道——`calc_change()` 對同一組期間直接回 `None`，明文拒絕給這個數字。

第 5 道是為了讓「驗過沒問題」和「根本沒驗」在畫面上分得出來。實測 EAP 答台灣人壽
前三季 177 億，本地沒收錄這筆，原本安靜跳過——看起來跟「四筆全驗過」一模一樣，
等於默認了那個沒驗過的數字。三種提示用三種顏色：紅（數字不符）、琥珀（比較無效）、
灰藍（沒驗過），不要讓使用者分不清嚴重程度。

## 卡住的時候，先看這裡

**跑起來說 `AuthenticationError`**
代表金鑰沒填對，回去檢查 `.env` 裡面那串金鑰有沒有貼對、有沒有多餘的空格。

**跑起來說 `ModuleNotFoundError`，說找不到某個套件**
先看終端機最前面有沒有 `(venv)` 這幾個字，沒有的話代表虛擬環境沒有啟動，回到第二步重新執行 `source venv/bin/activate`（Windows 是 `venv\Scripts\activate`）。

**跟資料庫（ChromaDB）有關的報錯**
通常是資料夾權限問題。注意：`vector_db` 現在裝著整個知識庫，**不要整個刪掉**（刪了就要重跑所有 PDF 的 VLM 解析，很花時間也花額度）。
先試著把 `vector_db/` 以外的東西排除，真的要重建的話，記得 `git checkout vector_db` 可以還原成版控裡的版本。

**改了前端，畫面卻沒變**
瀏覽器快取。把 `web/index.html` 裡 `style.css?v=2` 和 `app.js?v=2` 的數字 +1，或按 Ctrl+F5 強制重新整理。

**完全不知道錯誤訊息在講什麼**
直接把錯誤訊息整段複製貼給我，我幫你看。

## 維護：知識庫的單位怎麼補

`src/backfill_units.py` 是維護腳本，它會拿 `outputs/parsed_*.json`（VLM 的原始解析結果）跟指標庫對帳，
補上漏掉的單位、校正換算錯的單位。只會動 `unit` 這個欄位，不新增不刪除不改數值，所以重跑很安全。

```bash
venv/Scripts/python.exe src/backfill_units.py            # 先看它想改什麼（不寫入）
venv/Scripts/python.exe src/backfill_units.py --apply    # 確認沒問題再實際執行
```

它刻意保守：只有「數值也對得上」才套用單位（同一份簡報常在不同頁重複用同一個指標名稱、單位卻不同），
而且只自動修「確定是程式換算錯」的那些；兩份解析講法不同的會列出來讓你自己判斷，不擅自改。

## 部署（讓別人也能用網址連進來）

### 第一步：把專案放到 GitHub
1. 去 https://github.com 註冊帳號（如果還沒有）
2. 建立一個新的 repository（右上角 + 號 → New repository），設成 Public 或 Private 都可以
3. 在 VSCode 終端機（在 `hybrid-rag-project` 最外層）執行：
```powershell
git init
git add .
git commit -m "first version"
git branch -M main
git remote add origin 你的repo網址
git push -u origin main
```
`.env`（你的金鑰）因為 `.gitignore` 已經設定好，不會被上傳。
`vector_db/` 則是**刻意上傳**的——雲端只會從 repo 拉檔案，知識庫不跟著上去的話部署出來會是空的。

### 第二步之 A：部署到 Render（推薦，跑的是主要介面）
專案裡已經有 `render.yaml`，所以不用手動設定：
1. 打開 https://render.com，用 GitHub 帳號登入
2. New → **Blueprint**，選你剛剛建立的 repository，它會自動讀 `render.yaml`
3. 它會問你四個環境變數的值，填進去（這些不進版控）：
   `GEMINI_API_KEY`、`EAP_API_BASE_URL`、`EAP_PROJECT_ID`、`EAP_API_KEY`
   ——只用 Gemini 不用 EAP 的話，後三個可以留空
4. 按下部署，等它裝完套件啟動

### 第二步之 B：部署到 Streamlit Community Cloud（跑的是備援介面）
1. 打開 https://share.streamlit.io，用 GitHub 帳號登入
2. 點 "New app"，選擇你的 repository
3. Main file path 填：`src/app.py`
4. 點 "Advanced settings" 展開，在 "Secrets" 欄位貼上（格式是 TOML）：
```toml
GEMINI_API_KEY = "你的金鑰"
```
5. 點 "Deploy"

### 部署後要注意的事
- **後來上傳的資料不會永久保存**：雲端重啟或你更新程式碼時，容器會回到 repo 裡的狀態。
  跟著 repo 一起上去的知識庫還在，但你在網站上「上傳新資料」匯入的東西會不見。
  要永久保留的話，得在本機匯入後把 `vector_db/` 一起 commit 上去。
- **額度是共用的**：所有連進這個網址的人都共用同一組 Gemini 免費額度，人多的時候容易撞到「每分鐘／每天」的限制
- 更新程式碼後 `git push`，兩個平台都會自動重新部署
