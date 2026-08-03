# invest_agent

> 用以規範在股市情緒交易的人們所建置出的小助手

台股波段策略 + 交易前檢查工具。策略本體在 [STRATEGY.md](STRATEGY.md)。

搭配 [Claude Code](https://claude.com/claude-code) 使用：安裝後直接說「我想買 X」「幫我看持股」，
會觸發 `trade-check` skill，由 Claude 依 `STRATEGY.md` 的規則逐條檢查你這筆交易，
擋下違規的部分並給出具體的修正數字。

---

## ⚠️ 免責聲明

**本專案不是投資建議。** 它是一套把交易紀律寫成程式的個人工具，公開分享僅供參考與學習。

- 所有門檻與參數推導自 **2023-07 ~ 2026-07** 的台股資料，該期間加權指數上漲 **152%**。
  **在該樣本內，買進持有的報酬高於本專案所有的擇時規則。**
- 樣本僅包含 **7 段超過 5% 的回撤**，門檻數字有明顯的過度配適風險，真正的空頭市場中表現會顯著更差。
- 回測不含手續費、證交稅、融資利息與滑價。實際績效會更低。
- 使用本專案造成的任何損益由使用者自負。**請先讀完 [STRATEGY.md](STRATEGY.md) §12 已知限制。**

---

**策略共用，參數個人化。** 所有人共用同一份 `STRATEGY.md` 與腳本；
淨資產、是否用融資、單筆風險 %、槓桿上限、個人弱點，各自寫在 `profiles/<名字>.json`
（已列入 `.gitignore`，不會被推上共用倉庫）。

## 安裝（每台機器一次）

```bash
./install.sh 你的名字
```

會建立虛擬環境、產生 `profiles/你的名字.json`、安裝 `trade-check` skill、必要時建立大盤資料。
若偵測到虛擬環境的路徑與現況不符（資料夾曾搬移或改名），會自動重建。

> **skill 有兩份**：`.claude/skills/`（隨倉庫散布，clone 下來在本目錄開 Claude Code 就能用）
> 與 `~/.claude/skills/`（install.sh 產生，在任何目錄都能觸發）。兩份由同一個範本產生，不會走鐘。

裝完**務必編輯 `profiles/你的名字.json`**，至少填 `net_worth`，否則腳本會拒絕執行。
`notes` 欄位寫你自己的交易弱點，Claude 每次檢查時會特別盯那幾點。

> 需要對方自己有 Claude Code。`envest_agent/` 虛擬環境**不可直接複製**
> （裡面是硬編碼路徑的執行檔），一定要用 `install.sh` 重建。

## 授權

程式碼採 MIT License（見 [LICENSE](LICENSE)）。

`STRATEGY.md` §11 引用並評論了第三方付費電子報（MimiVsJames 美股輕鬆談）的方法論，
屬合理使用之評論與比較。**原文不隨本倉庫散布**（`文章/` 已列入 `.gitignore`），
需要原文請自行向該作者訂閱。

## 目錄結構

```
invest_agent/
├── install.sh           安裝腳本
├── STRATEGY.md          策略（共用，改門檻改這裡）
├── profiles/            ← 個人參數，一人一份，不進倉庫
│   ├── example.json          範本
│   └── <名字>.json           你的
├── paths.py             檔案路徑集中管理
├── profile_loader.py    個人設定載入器
├── market_state.py      每日：盤面狀態、槓桿上限、部位反推
├── portfolio_check.py   每日：持股曝險、追繳線、逐檔出場動作
├── positions.py         持股紀錄（成本／當初停損／融資別）單一事實來源
├── buy_check.py         買進前檢查，可盤中用（即時價 + 既有部位曝險）；--asof 可重現當時判斷
├── replay.py            交易回放：某天某價買了，之後逐日照 §6 規則會發生什麼
├── skill/               trade-check skill 範本（install.sh 依此產生下面兩份）
├── .claude/skills/      專案版 skill（相對路徑，隨倉庫散布）
├── fetch/               資料取得與清洗
├── research/            門檻推導過程（每季重跑驗證，不要刪）
├── data/                ← 本機生成，不隨倉庫散布（install.sh 會建檔）
│   ├── taiex_daily.csv       大盤原始價量（逐日累積，重爬成本高）
│   ├── stocks_daily.csv      所有個股/ETF 原始價量（用 sid 區分）
│   ├── taiex_enriched.csv    衍生：由 research/analyze.py 產生
│   └── stocks_adj.csv        衍生：由 fetch/clean_stocks.py 產生（分割還原）
└── 文章/                ← 本機自備，不隨倉庫散布（第三方付費內容）
    ├── *.txt                 抽出的純文字（Claude 讀這個）
    └── 原始/*.pdf, *.docx    來源檔
```

> `data/` 與 `文章/` 都在 `.gitignore` 內。clone 下來不會有這兩個目錄，
> `data/` 由 `install.sh` 自動建立，`文章/` 是選用的（放你自己訂閱的研究文章）。

## 每日流程

```bash
# 1. 看盤面（會自動更新最近兩個月資料）
./envest_agent/bin/python market_state.py

# 2. 檢視持股（自動讀持股紀錄，不必每天重打成本與停損）
./envest_agent/bin/python portfolio_check.py

# 3. 想買東西時（可盤中，會抓即時價並加總既有部位的曝險）
./envest_agent/bin/python buy_check.py 3481 100
```

## 持股紀錄

成本與**當初設定的停損價**只該輸入一次，之後每天讀紀錄。存在
`profiles/<名字>_positions.json`（已在 .gitignore 內）。

```bash
./envest_agent/bin/python positions.py                            # 列出
./envest_agent/bin/python positions.py --add 3481:20:45.5:41.0    # SID:張數:成本:停損[:cash]
./envest_agent/bin/python positions.py --close 3481:42.0:停損      # 出場（原因用「停損」會自動計數）
./envest_agent/bin/python positions.py --reconcile 3481:20:45.5   # 與紀錄比對，加 --apply 才寫入
```

同一檔 30 天內停損 2 次，`positions.py` 與 `buy_check.py` 都會依 §7 擋下並顯示解禁日。

多人共用同一份專案時，加 `--profile <名字>` 或設定環境變數 `INVEST_PROFILE`。

或直接在 Claude Code 說「幫我看持股」「我想買 X」，會觸發 `trade-check` skill。

## ⚠ 給第一次使用的人

規則的價值不是提高報酬，是把最大回撤從 -28.7% 壓到 -11~15%，**讓槓桿的使用成為可能**。
若你本來就不用槓桿、也抱得住回撤，買進持有可能更適合你——這點見頂端免責聲明。

`profiles/example.json` 預設 `margin_allowed: false`、槓桿上限保守。
**在你確定自己承受得住之前，不要調高。**

## 新增標的

```bash
# 新標的（上市或上櫃都行，會自動判斷）
./envest_agent/bin/python fetch/fetch_stocks.py 2454 6182 --since 2023-07

# 更新既有標的：每檔各自從自己最後一筆補到現在，不會留空洞
./envest_agent/bin/python fetch/fetch_stocks.py

./envest_agent/bin/python fetch/clean_stocks.py      # 補完一定要跑，還原分割與減資
```

資料會**併入** `data/stocks_daily.csv`，不會產生新檔案。
執行後會列出**缺漏檢查**（以大盤交易日為基準），區分「抓取失敗」與「真停牌」——
前者要補抓，後者由 `clean_stocks.py` 自動還原。交易所改名（如 00631L）也會自動統一。

> ⚠ TWSE 的 `STOCK_DAY` 回傳**未還原權值的原始價**。0050 於 2025-06-18 分割 1:4、
> 00631L 於 2026-03-31 分割約 1:22，不還原會得到「0050 三年報酬 -21%」這種荒謬結果。
> 減資則相反，造成**向上**跳空（群創 3481 於 2024-08 一次 +14.4%），不還原會虛增報酬。
> `clean_stocks.py` 以「大盤有交易而該檔沒有」判定停牌，用大盤同期漲跌還原復牌日的報酬，
> 兩個方向都涵蓋，且錨定在最新成交價——還原後的價格等於實際盤面價格，可以直接拿來下單。

## 新增文章（選用）

把你自己訂閱的研究文章丟進 `文章/原始/`（此目錄不隨倉庫散布），然後：

```bash
./envest_agent/bin/python fetch/extract_articles.py
```

## 更新大盤歷史資料

```bash
./envest_agent/bin/python fetch/fetch_twse.py            # 增量（最近兩個月）
./envest_agent/bin/python fetch/fetch_twse.py --full     # 重建（2023-07 至今）
```

## 每季該做的事

[STRATEGY.md](STRATEGY.md) §12 說明門檻（-8%、-12%、ATR% 2.5）樣本只有 7 段回撤，
有過度配適風險。每季重跑一次 `research/` 下的腳本，確認門檻在新資料上仍成立：

```bash
for f in research/analyze*.py; do ./envest_agent/bin/python "$f"; done
```

## 驗證過去的交易

```bash
./envest_agent/bin/python buy_check.py 3481 100 --asof 2026-06-17 --price 55   # 當時該不該買
./envest_agent/bin/python replay.py 3481 --entry 2026-06-17 --price 55 --lots 100
```

兩者都只用指定日期（含）以前的資料，`buy_check --asof` 還會排除當時尚未建立的部位。

## 檔案保留原則

| 類別 | 處置 |
|---|---|
| `data/taiex_daily.csv`、`data/stocks_daily.csv` | **永久保留**，重爬成本高，每天只長約 90 bytes |
| `data/taiex_enriched.csv`、`data/stocks_adj.csv` | 可刪，幾秒即可重生。**改了原始資料務必重跑** |
| `research/analyze*.py` | 歸檔別刪，每季要拿它驗證門檻 |
| `文章/原始/` | 保留（來源憑證、含圖表） |
| log 檔 | 直接刪 |
