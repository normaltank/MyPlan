async (page) => {
    console.log("Starting harvest script in Playwright MCP...");
    const browser = page.context().browser();
    const context = await browser.newContext({
        userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        locale: 'zh-CN'
    });
    const p = await context.newPage();

    // 1. Initial navigation to establish Ruishu session
    console.log("Step 1: Establishing Ruishu session...");
    try {
        await p.goto('https://zylcxy.gszy.edu.cn/html/cid/1.html', { timeout: 30000 });
        await p.waitForTimeout(2000);
    } catch (e) {
        console.log("Ruishu init notice:", e.message);
    }

    // 2. Fetch department roster from /page/cid/40.html
    console.log("Step 2: Fetching department rosters...");
    let deptMap = {};
    try {
        await p.goto('https://zylcxy.gszy.edu.cn/page/cid/40.html', { timeout: 20000 });
        await p.waitForTimeout(1000);
        const orgText = await p.evaluate(() => document.body.innerText);
        let curDept = '';
        for (const line of orgText.split('\n')) {
            const trimmed = line.trim();
            if (trimmed.includes('教研室') || trimmed.includes('办公室')) {
                curDept = trimmed;
            } else if (curDept && trimmed) {
                const names = trimmed.split(/[、,，\s]+/);
                for (let n of names) {
                    n = n.trim();
                    if (n.length >= 2 && n.length <= 4 && /^[\u4e00-\u9fa5]+$/.test(n)) {
                        deptMap[n] = curDept;
                    }
                }
            }
        }
    } catch (e) {
        console.log("Org fetch error:", e.message);
    }
    console.log(`Mapped ${Object.keys(deptMap).length} teachers to departments.`);

    // 3. Iterate through 12 pages of 导师风采
    console.log("Step 3: Gathering teacher links from 12 pages...");
    let teacherCards = [];
    for (let pg = 1; pg <= 12; pg++) {
        const pageUrl = `https://zylcxy.gszy.edu.cn/teacher/cid/36.html?cid=36&page=${pg}`;
        try {
            await p.goto(pageUrl, { timeout: 20000 });
            await p.waitForTimeout(600);
            const items = await p.evaluate(() => {
                const links = Array.from(document.querySelectorAll('a[href*="show_teacher"]'));
                return links.map(a => {
                    const h3 = a.querySelector('h3, h4, .name, .title');
                    const img = a.querySelector('img');
                    return {
                        href: a.href,
                        name: h3 ? h3.innerText.trim() : a.innerText.trim().split('\n')[0].trim(),
                        cardText: a.innerText.trim(),
                        imgSrc: img ? img.src : ''
                    };
                });
            });
            console.log(`  Page ${pg} found ${items.length} cards.`);
            for (const item of items) {
                const cleanName = item.name.split(/\s+/)[0].trim();
                if (cleanName && item.href) {
                    teacherCards.push({
                        name: cleanName,
                        href: item.href,
                        cardText: item.cardText,
                        imgSrc: item.imgSrc
                    });
                }
            }
        } catch (e) {
            console.log(`  Error on page ${pg}:`, e.message);
        }
    }
    console.log(`Total teacher cards gathered: ${teacherCards.length}`);

    // Helper functions
    function cleanBio(text) {
        if (!text) return '';
        const lines = text.split('\n');
        const cleanLines = [];
        for (let l of lines) {
            l = l.trim();
            if (!l) continue;
            if (/点击量：\s*\d+次/.test(l)) continue;
            if (l.startsWith('上一篇：') || l.startsWith('下一篇：') || l.startsWith('【字体：') || l.startsWith('上一条：') || l.startsWith('下一条：')) continue;
            if (l === '当前位置：首页>导师风采' || l === '导师风采') continue;
            cleanLines.push(l);
        }
        return cleanLines.join('\n');
    }

    function extractTitle(text, fallback) {
        if (!text) return fallback || '教师';
        const titles = [
            "讲席教授", "特聘教授", "二级教授", "三级教授", "四级教授",
            "教授", "副教授", "助理教授", "讲师", "助教",
            "主任医师", "副主任医师", "主治医师", "住院医师",
            "研究员", "副研究员", "助理研究员",
            "高级实验师", "实验师", "助理实验师",
            "高级工程师", "工程师", "助理工程师"
        ];
        for (const t of titles) {
            if (text.includes(t)) return t;
        }
        return fallback || '教师';
    }

    function extractEmail(text) {
        if (!text) return '';
        const regex = /[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+/g;
        const matches = text.match(regex);
        if (!matches) return '';
        const publicPrefixes = ['office@', 'admin@', 'dean@', 'info@', 'contact@', 'yjs@', 'postmaster@', 'webmaster@', 'news@', 'service@'];
        for (const m of matches) {
            const ml = m.toLowerCase();
            if (publicPrefixes.some(p => ml.startsWith(p))) continue;
            if (ml.endsWith('.png') || ml.endsWith('.jpg') || ml.endsWith('.jpeg') || ml.endsWith('.gif') || ml.endsWith('.css') || ml.endsWith('.js')) continue;
            return m;
        }
        return '';
    }

    const todayStr = '2026-09-24';
    const records = [];
    const seenNames = new Set();

    // 4. Visit each teacher's profile
    console.log("Step 4: Extracting full biographies from profiles...");
    for (let i = 0; i < teacherCards.length; i++) {
        const card = teacherCards[i];
        let bio = '';
        let title = '教师';
        let email = '';
        
        try {
            await p.goto(card.href, { timeout: 20000 });
            await p.waitForTimeout(200);
            const content = await p.evaluate(() => {
                const c = document.querySelector('#fontzoom, .base_news_main, .content');
                return c ? c.innerText.trim() : document.body.innerText.trim();
            });
            bio = cleanBio(content);
            title = extractTitle(bio, extractTitle(card.cardText, '教师'));
            email = extractEmail(bio) || extractEmail(card.cardText);
        } catch (e) {
            console.log(`  Error on ${card.name}:`, e.message);
            bio = cleanBio(card.cardText);
            title = extractTitle(bio, '教师');
            email = extractEmail(bio);
        }

        seenNames.add(card.name);
        records.push({
            '姓名': card.name,
            '职称': title,
            '邮箱': email,
            '学校': '甘肃中医药大学',
            '学院': '中医临床学院',
            '采集人': 'Antigravity',
            '采集日期': todayStr,
            '简介': bio,
            '主页': card.href
        });
        
        if ((i + 1) % 20 === 0 || i === teacherCards.length - 1) {
            console.log(`  Progress: ${i + 1}/${teacherCards.length} teachers processed.`);
        }
    }

    // 5. Add department faculty members who weren't in 导师风采
    console.log("Step 5: Adding department members from roster...");
    for (const [name, dept] of Object.entries(deptMap)) {
        if (!seenNames.has(name)) {
            let title = '讲师';
            if (dept.includes('办公室')) title = '工作人员';
            records.push({
                '姓名': name,
                '职称': title,
                '邮箱': '',
                '学校': '甘肃中医药大学',
                '学院': '中医临床学院',
                '采集人': 'Antigravity',
                '采集日期': todayStr,
                '简介': `甘肃中医药大学中医临床学院${dept}成员。`,
                '主页': 'https://zylcxy.gszy.edu.cn/page/cid/40.html'
            });
            seenNames.add(name);
        }
    }

    await context.close();
    return records;
}
