# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: intelligence-live.spec.ts >> validated reviewer live writes stay single under synchronous duplicate activation
- Location: tests/e2e/intelligence-live.spec.ts:6:5

# Error details

```
Error: expect(page).toHaveScreenshot(expected) failed

  49714 pixels (ratio 0.10 of all image pixels) are different.

  Snapshot: intelligence-live-verdict.png

Call log:
  - Expect "toHaveScreenshot(intelligence-live-verdict.png)" with timeout 5000ms
    - verifying given screenshot expectation
  - taking page screenshot
    - disabled all CSS animations
  - waiting for fonts to load...
  - fonts loaded
  - 49714 pixels (ratio 0.10 of all image pixels) are different.
  - waiting 100ms before taking screenshot
  - taking page screenshot
    - disabled all CSS animations
  - waiting for fonts to load...
  - fonts loaded
  - captured a stable screenshot
  - 49714 pixels (ratio 0.10 of all image pixels) are different.

```

# Page snapshot

```yaml
- generic [ref=e2]:
  - link "跳到主要内容" [ref=e3] [cursor=pointer]:
    - /url: "#main-content"
  - complementary [ref=e4]:
    - navigation "Intelligence Web 主导航" [ref=e5]:
      - button "案件" [ref=e6] [cursor=pointer]:
        - generic [ref=e7]: 案件
      - button "Claim 矩阵" [ref=e8] [cursor=pointer]:
        - generic [ref=e9]: Claim 矩阵
      - button "多源证据" [ref=e10] [cursor=pointer]:
        - generic [ref=e11]: 多源证据
      - button "传播关系" [ref=e12] [cursor=pointer]:
        - generic [ref=e13]: 传播关系
      - button "页面历史" [ref=e14] [cursor=pointer]:
        - generic [ref=e15]: 页面历史
      - button "模型准入" [ref=e16] [cursor=pointer]:
        - generic [ref=e17]: 模型准入
      - button "裁决与申诉" [ref=e18] [cursor=pointer]:
        - generic [ref=e19]: 裁决与申诉
      - button "证据包" [ref=e20] [cursor=pointer]:
        - generic [ref=e21]: 证据包
  - generic [ref=e22]:
    - banner [ref=e23]:
      - button "租户 · e_live · 真实调查联调项目 ⌄" [ref=e24] [cursor=pointer]
      - generic [ref=e25]:
        - button "通知" [ref=e26] [cursor=pointer]: ◌
        - generic "用户 · e_live" [ref=e27]: 用
    - main [ref=e28]:
      - generic [ref=e29]:
        - generic [ref=e30]:
          - text: Intelligence Web
          - heading "证据调查台" [level=1] [ref=e31]
          - paragraph [ref=e32]: 从原子 Claim、多源证据与传播关系形成可解释的人工裁决。
        - generic [ref=e33]:
          - button "导出视图" [ref=e34] [cursor=pointer]
          - button "创建任务" [ref=e35] [cursor=pointer]
      - generic [ref=e36]:
        - generic [ref=e37]:
          - generic [ref=e38]:
            - generic [ref=e39]:
              - text: Human decision
              - heading "人工裁决" [level=2] [ref=e40]
            - generic [ref=e41]: confirmed
            - generic [ref=e42]: 真实 intelligence API
          - generic [ref=e43]:
            - generic [ref=e44]: GEO 可能性
            - strong [ref=e45]: "0.73"
            - generic [ref=e48]: 不确定性 19% · 证据充分度 82%
          - paragraph [ref=e49]: 规则版本：geo-rule-v2
          - list [ref=e50]:
            - listitem [ref=e51]: 两个独立来源簇共同支持该 Claim
            - listitem [ref=e52]: 仍存在 19% 不确定性，必须由人工裁决
          - generic [ref=e53]:
            - button "证据不足，不成立" [ref=e54] [cursor=pointer]
            - button "确认高风险表述" [ref=e55] [cursor=pointer]
          - status [ref=e56]: 真实人工裁决已记录
        - complementary [ref=e57]:
          - heading "复核与申诉" [level=2] [ref=e58]
          - paragraph [ref=e59]: 申诉不会覆盖原裁决；新事实会创建独立版本和审计事件。
          - generic [ref=e60]:
            - generic [ref=e61]:
              - generic [ref=e62]: 申诉理由
              - textbox "申诉理由" [active] [ref=e63]: 补充新的独立来源并申请重新复核
            - button "提交申诉" [disabled] [ref=e64]
            - text: 申诉由分析师提交，审核人不能代为发起。
```

# Test source

```ts
  194 |             const style = getComputedStyle(candidate, pseudo);
  195 |             for (let index = 0; index < style.length; index += 1) {
  196 |               if (style.getPropertyValue(style.item(index)).includes('url(')) return true;
  197 |             }
  198 |           }
  199 |           return false;
  200 |         });
  201 |         return {
  202 |           payloadFree:
  203 |             element instanceof HTMLDivElement &&
  204 |             element.dataset.visualEvidence === 'payload-free' &&
  205 |             element.querySelector('img,canvas,svg,picture,video,object,embed') === null &&
  206 |             !hasResourceAttribute &&
  207 |             !candidates.some((candidate) => candidate.hasAttribute('style')) &&
  208 |             !hasComputedResource,
  209 |         };
  210 |       });
  211 |     return {
  212 |       textLines: (document.body.innerText ?? '').split(/\r?\n/u),
  213 |       textNodes,
  214 |       attributeNames,
  215 |       attributes,
  216 |       controls: Array.from(
  217 |         document.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(
  218 |           'input,textarea,select',
  219 |         ),
  220 |         (control) => control.value,
  221 |       ),
  222 |       machineReadableVisuals,
  223 |       url: location.href,
  224 |       cookie: document.cookie,
  225 |       historyState: (() => {
  226 |         try {
  227 |           return JSON.stringify(history.state) ?? null;
  228 |         } catch {
  229 |           return null;
  230 |         }
  231 |       })(),
  232 |       localStorage: Object.entries(localStorage).map(([key, item]) => ({
  233 |         key,
  234 |         value: item,
  235 |       })),
  236 |       sessionStorage: Object.entries(sessionStorage).map(([key, item]) => ({
  237 |         key,
  238 |         value: item,
  239 |       })),
  240 |     };
  241 |   });
  242 |   return screenshotSurfaceIssues(value);
  243 | }
  244 | 
  245 | function assertSafeSnapshotName(name: string): void {
  246 |   if (!snapshotNamePattern.test(name)) {
  247 |     throw new Error('Visual snapshot name must be one fixed PNG basename.');
  248 |   }
  249 | }
  250 | 
  251 | function resolveSafeEvidencePath(path: string): string {
  252 |   const target = isAbsolute(path) ? resolve(path) : resolve(process.cwd(), path);
  253 |   const contained = visualEvidenceRoots.some((root) => {
  254 |     const child = relative(root, target);
  255 |     return (
  256 |       child.length > 0 && child !== '..' && !child.startsWith(`..${sep}`) && !isAbsolute(child)
  257 |     );
  258 |   });
  259 |   if (!contained || !target.endsWith('.png')) {
  260 |     throw new Error('Visual evidence path must be a PNG inside an approved test evidence root.');
  261 |   }
  262 |   return target;
  263 | }
  264 | 
  265 | async function requireSafeScreenshotSurface(page: Page): Promise<void> {
  266 |   const issues = await inspectScreenshotSurface(page);
  267 |   expect(
  268 |     issues,
  269 |     `Visual evidence rejected by DLP (${issues.join(',')}); raw rendered values are intentionally omitted.`,
  270 |   ).toEqual([]);
  271 | }
  272 | 
  273 | export async function captureSafeScreenshot(
  274 |   page: Page,
  275 |   options: PageScreenshotOptions & { path: string },
  276 | ): Promise<void> {
  277 |   const target = resolveSafeEvidencePath(options.path);
  278 |   const issues = await inspectScreenshotSurface(page);
  279 |   if (issues.length > 0) await rm(target, { force: true });
  280 |   expect(
  281 |     issues,
  282 |     `Visual evidence rejected by DLP (${issues.join(',')}); raw rendered values are intentionally omitted.`,
  283 |   ).toEqual([]);
  284 |   await page.screenshot({ ...options, path: target });
  285 | }
  286 | 
  287 | export async function expectSafePageScreenshot(
  288 |   page: Page,
  289 |   name: string,
  290 |   options?: PageAssertionsToHaveScreenshotOptions,
  291 | ): Promise<void> {
  292 |   assertSafeSnapshotName(name);
  293 |   await requireSafeScreenshotSurface(page);
> 294 |   await expect(page).toHaveScreenshot(name, options);
      |                      ^ Error: expect(page).toHaveScreenshot(expected) failed
  295 | }
  296 | 
  297 | export async function expectSafeLocatorScreenshot(
  298 |   page: Page,
  299 |   locator: Locator,
  300 |   name: string,
  301 |   options?: {
  302 |     animations?: 'disabled' | 'allow';
  303 |     caret?: 'hide' | 'initial';
  304 |     maxDiffPixelRatio?: number;
  305 |     maxDiffPixels?: number;
  306 |     omitBackground?: boolean;
  307 |     timeout?: number;
  308 |   },
  309 | ): Promise<void> {
  310 |   assertSafeSnapshotName(name);
  311 |   await requireSafeScreenshotSurface(page);
  312 |   await expect(locator).toHaveScreenshot(name, options);
  313 | }
  314 | 
```