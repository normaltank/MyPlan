# 高校教师采集相似案例核查

核查日期：2026-09-26。范围：公开论文、作者仓库源码、官方研究人员数据文档；未运行外部采集器、未调用模型或读取本项目凭据。下列建议是研究推论，尚未在本项目验证。

## 结论

最接近的是 **academic_census 的院系教师普查**；一个较小的 **university-scraper** 可用来对照配置式提取。**ORCID、OpenAlex** 更适合学习身份、机构、时间与来源建模。此次核查没有找到可以直接代替本项目“真实浏览器侦察、模型仅输出结构计划、确定性全文提取、大学双 Sheet 守恒”整套履约流程的经验证方案。

| 案例 | 相似处 | 证据类型 | 本项目应学什么 |
| --- | --- | --- | --- |
| academic_census | 跨院系教师目录发现和结构解析 | 同行评审论文、作者补充代码 | 分阶段评估、目录覆盖、原始页面回放 |
| MagusDev/university-scraper | 大学/院系配置、个人主页字段提取 | 直接检查源码 | 结构配置与数据记录分别持久化 |
| ORCID | 科研人员、机构、职务及个人主页 | 官方 API 与机构工作流 | 身份与任职关系分开、来源和有效时间 |
| OpenAlex | 作者身份消歧与机构关联 | 官方数据字典 | 外部标识作为辅助证据、保留关联来源 |

## 1. academic_census：直接相似的学术普查

- **项目**：[作者仓库](https://github.com/allisonmorgan/academic_census)；[论文全文](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0202223)。
- **确证功能**：论文报告从 205 个北美计算机院系入口定位目录，以 div/table/list/article 划分重复记录，并检查分页。阶段分别评估。[论文方法与结果](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0202223)
- **可借鉴**：目录发现、卡片结构、属性提取、对象范围拆开审计；用保存 HTML 做可重复比较。这是由方法推导出的本项目建议。
- **不能照搬**：姓名/职称白名单、职称黑名单，以及无职称时丢弃记录，均不适合项目红线和一人不漏要求；全文履历采集并非该论文完成的功能。[方法](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0202223)；[实际辅助词表源码](https://github.com/allisonmorgan/academic_census/blob/f905cb05b5c0024157d888e11453661cd191a56a/misc/helpers.py)
- **局限**：论文的高准确率属于历史英语目录研究；509 人职称仍由人工补齐，速度测量使用预先下载 HTML。作者仓库是补充数据与分析代码，不能称为已经核实可部署的完整浏览器采集器。[论文结果](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0202223)；[仓库说明](https://github.com/allisonmorgan/academic_census)

## 2. MagusDev/university-scraper：配置式教师主页采集

- **项目**：[仓库](https://github.com/MagusDev/university-scraper)；[核查版本源码](https://github.com/MagusDev/university-scraper/blob/aa7229ecda00da6d73597b5b95238f8b931f5959/UI.py)。
- **确证功能**：实际代码保存大学、院系、列表容器、姓名和邮箱标签/类；遍历列表卡片中的链接，访问主页，以 BeautifulSoup 提取，并写 SQLite、导出 CSV。已查看 `scrape`、`extract_matches`、建表及插入代码。[源码](https://github.com/MagusDev/university-scraper/blob/aa7229ecda00da6d73597b5b95238f8b931f5959/UI.py)
- **可借鉴**：将学院结构配置和教师记录分开保存，便于结构失效时只重新侦察配置。配置还应记录浏览器核实时间和证据位置，这是本项目建议。
- **不能照搬**：源码使用 requests，按研究关键词决定是否收录，保存的是关键词相关片段；不符合真实浏览器履约、全员名单与完整正文要求。[源码](https://github.com/MagusDev/university-scraper/blob/aa7229ecda00da6d73597b5b95238f8b931f5959/UI.py)
- **局限**：这是源码审阅，未安装或实测。README 的 MIT/Stanford 配置不是本轮核实的官方选择器，也不是采集成功证据。[README](https://github.com/MagusDev/university-scraper)

## 3. ORCID：有来源和时间的任职关系

- **项目**：[ORCID 官方](https://info.orcid.org/)；[读取字段文档](https://info.orcid.org/ufaqs/what-sections-can-i-read-on-orcid-record-using-the-api/)。
- **确证功能**：官方 API 列出任职、个人资料、邮箱和研究者主页等分段端点；公开可读范围受条目可见性限制。[字段](https://info.orcid.org/ufaqs/what-sections-can-i-read-on-orcid-record-using-the-api/)；[可见性 FAQ](https://info.orcid.org/documentation/integration-and-api-faq/)
- **官方案例**：任职工作流展示 Oxford 的机构添加记录示例，并定义母机构、部门、职务、URL、起止日期与机构标识。[机构任职工作流](https://info.orcid.org/documentation/workflows/employment-workflow/)
- **可借鉴**：在审计层区分人物身份、大学、学院、任职关系、事实来源与有效期；历史任职不能自动当作当前在职。这是数据模型建议。
- **不能照搬**：ORCID 不是学院全量名录；公开字段可能缺失，机构流程涉及研究者授权。不可通过姓名近似匹配后直接覆盖官网职称或邮箱。[任职工作流](https://info.orcid.org/documentation/workflows/employment-workflow/)；[可见性 FAQ](https://info.orcid.org/documentation/integration-and-api-faq/)
- **局限**：核实了官方契约及示例，没有对目标高校调用 API 或测量覆盖率；本项目若今后使用外部资料，需单独设计来源优先级。

## 4. OpenAlex：作者实体与机构关联

- **项目**：[OpenAlex 官方作者文档](https://help.openalex.org/data/authors/)。
- **确证功能**：作者具有稳定 ID、名称变体、ORCID、作品与机构关联；机构历史由作品 authorships 推导，`last_known_institutions` 来自最近作品，不是人事系统在职证明。[官方数据字典](https://help.openalex.org/data/authors/)
- **可借鉴**：内部保留稳定实体标识与原始来源关联；外部作者 ID 可作为候选核对信号，不能成为单独的同一人判据。这是基于官方实体语义的设计推论。
- **不能照搬**：作者覆盖面和高校专任教师范围不同，论文机构也不等于当前学院归属；主题与指标不能替代官网简介全文。不能用其作者数量证明学院名录完整。[官方实体定义](https://help.openalex.org/data/authors/)
- **局限**：本轮只核实官方文档，未测试人物匹配或 API；作者消歧本身存在误合并、误拆分，官方作者页链接了这些失败模式。[官方作者说明](https://help.openalex.org/data/authors/)

## 对本项目的应用顺序（研究建议）

1. **先补覆盖证据**：学院名录分支、每分支页数、每页卡片数、去重候选数、已访问主页、失败项分别记录；双 Sheet 守恒只证明已收录记录分表正确。
2. **建立页面回放集**：保留匿名任务标识、官方 URL、浏览器取得的 HTML/截图、已核实选择器和审计结果；抽取回归无需重新调用模型。按实际结构变化选样，覆盖分页、tab、混合人员目录及图片名录。
3. **增加字段来源审计**：采集值保留原文与来源 URL，职称缺失作为明确待核实状态；不得以推断、删除人员或模型补写来补齐。
4. **再考虑身份关联**：官网资料为当前交付依据；ORCID/OpenAlex 先作为独立候选核对来源，避免外部机构历史污染大学合并与同名教师去重。

这些建议不要求更换采集内核，不改变学院独立 Harness 与大学交付边界；尚需主控结合现有实现判断哪些已具备、哪些是真缺口。

## 检索排除项

搜索也命中 [Sukarth/faculty-scraper](https://github.com/Sukarth/faculty-scraper)：作者明确宣称用 Gemini 提取教师信息，和本项目模型用途边界冲突，故不列为可采用采集方案。另有若干仓库的 README 宣称跨校全量能力；未获得可靠代码或实测证据的功能没有进入上述结论。
