// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router';
import Shell from './shell';

describe('Customer platform account lifecycle', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    history.replaceState(null, '', '/platform/customer/');
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              status: 'mock-ready',
              service: 'geo-platform-v2',
              version: 'contract-v1',
            }),
            { status: 200, headers: { 'content-type': 'application/json' } },
          ),
      ),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('completes pairing and revocation without asking for or persisting secrets', async () => {
    const user = userEvent.setup();
    const { container } = render(<Shell />);

    await user.click(screen.getByRole('button', { name: /平台账号/ }));
    expect(screen.getByRole('heading', { name: '客户终端安全配对' })).toBeTruthy();
    expect(container.querySelector('input[type="password"]')).toBeNull();

    await user.clear(screen.getByLabelText('运营责任人'));
    await user.type(screen.getByLabelText('运营责任人'), '周岚');
    await user.selectOptions(screen.getByLabelText('托管模式'), 'customer-device');
    await user.click(screen.getByRole('button', { name: '登记授权' }));
    expect(await screen.findByText(/授权登记已更新/)).toBeTruthy();

    await user.click(screen.getByRole('button', { name: '创建一次性配对' }));
    expect(screen.getByText('请二次确认本次任务')).toBeTruthy();
    expect(screen.getByText(/请勿在聊天或普通表单粘贴验证码/)).toBeTruthy();

    await user.click(screen.getByRole('button', { name: '确认并生成配对码' }));
    expect(screen.getByRole('img', { name: /一次性安全配对二维码/ })).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '终端已连接' }));
    expect(screen.getByText('请在豆包原生页面完成验证')).toBeTruthy();
    expect(container.querySelector('input[type="password"]')).toBeNull();

    await user.click(screen.getByRole('button', { name: '模拟平台确认完成' }));
    expect(screen.getByText('配对与验证已完成')).toBeTruthy();
    expect(screen.getByText(/准入保持 read_verified/)).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '撤销授权' }));
    expect(screen.getByRole('heading', { name: '撤销已执行' })).toBeTruthy();
    expect(screen.getByText('删除托管秘密副本')).toBeTruthy();

    const forbidden = [
      'SESSION=',
      'Bearer ',
      'dlp-canary',
      '/secret/browser/profile',
      '13800138000',
    ];
    const surfaces = [
      container.textContent ?? '',
      location.href,
      JSON.stringify(localStorage),
      JSON.stringify(sessionStorage),
    ];
    for (const surface of surfaces)
      for (const secret of forbidden) expect(surface).not.toContain(secret);
  });

  it.each([
    ['拒绝', '本次配对已拒绝'],
    ['超时', '一次性配对已超时'],
  ])('supports the %s terminal state', async (outcome, expected) => {
    const user = userEvent.setup();
    render(<Shell />);
    await user.click(screen.getByRole('button', { name: /平台账号/ }));
    await user.click(screen.getByRole('button', { name: '创建一次性配对' }));
    if (outcome === '拒绝') {
      await user.click(screen.getByRole('button', { name: '拒绝' }));
    } else {
      await user.click(screen.getByRole('button', { name: '确认并生成配对码' }));
      await user.click(screen.getByRole('button', { name: '模拟超时' }));
    }
    expect(screen.getByRole('heading', { name: expected })).toBeTruthy();
    expect(screen.getByRole('button', { name: '重新开始' })).toBeTruthy();
  });

  it('validates truth confirmation and creates a new profile version', async () => {
    const user = userEvent.setup();
    render(<Shell />);
    await user.click(screen.getByRole('button', { name: '资料' }));
    await user.click(screen.getByRole('button', { name: '保存并生成版本' }));
    expect(screen.getByText('提交前必须确认资料真实性')).toBeTruthy();
    await user.click(screen.getByRole('checkbox', { name: /我确认上述客户声明真实/ }));
    await user.click(screen.getByRole('button', { name: '保存并生成版本' }));
    expect(await screen.findByText(/客户声明 v3/)).toBeTruthy();
  });

  it('adds a validated brand, product and customer-confirmed competitor', async () => {
    const user = userEvent.setup();
    render(<Shell />);
    await user.click(screen.getByRole('button', { name: '品牌产品' }));
    await user.type(screen.getByLabelText('品牌名称'), '澄明云');
    await user.clear(screen.getByLabelText('官方 HTTPS 网站'));
    await user.type(screen.getByLabelText('官方 HTTPS 网站'), 'https://example.test');
    await user.type(screen.getByLabelText('产品或服务'), '可信知识助手');
    await user.type(screen.getByLabelText('客户指定竞品'), '北辰智库');
    await user.click(screen.getByRole('button', { name: '登记资产' }));
    expect(screen.getByText('澄明云')).toBeTruthy();
    expect(screen.getByText('可信知识助手')).toBeTruthy();
    expect(screen.getByText('北辰智库')).toBeTruthy();
  });

  it('validates and submits a configuration request without mutating scheduling truth', async () => {
    const user = userEvent.setup();
    render(<Shell />);
    await user.click(screen.getByRole('button', { name: '问题目标' }));
    await user.click(screen.getByRole('button', { name: '提交审核' }));
    expect(screen.getByText('问题至少需要 8 个字')).toBeTruthy();
    expect(screen.getByText('请说明至少 10 个字的业务原因')).toBeTruthy();
    await user.type(screen.getByLabelText('关注问题'), '制造企业如何选择可信的私有化知识库？');
    await user.type(screen.getByLabelText('业务原因'), '需要覆盖客户采购决策阶段的真实比较问题。');
    await user.click(screen.getByRole('button', { name: '提交审核' }));
    expect(screen.getByText('待运营审核')).toBeTruthy();
    expect(screen.getByText('制造企业如何选择可信的私有化知识库？')).toBeTruthy();
  });

  it('paginates answers and opens an anchored evidence diff dialog', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <Shell />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole('button', { name: '回答证据' }));
    expect(screen.getByText('企业知识库如何选择？')).toBeTruthy();
    await user.click(screen.getAllByRole('button', { name: '查看回答截图' })[0]!);
    expect(screen.getByRole('dialog', { name: '证据与历史差异' })).toBeTruthy();
    expect(screen.getByRole('img', { name: /锚点高亮品牌提及/ })).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '关闭证据弹窗' }));
    await user.click(screen.getByRole('button', { name: '下一页' }));
    expect(await screen.findByText('私有化大模型方案对比')).toBeTruthy();
    await user.selectOptions(screen.getByLabelText('回答地域'), '上海');
    expect(await screen.findByText('企业知识库如何选择？')).toBeTruthy();
    expect(screen.getByText('第 1 / 1 页')).toBeTruthy();
  });

  it('submits a report question and records customer receipt confirmation', async () => {
    const user = userEvent.setup();
    render(<Shell />);
    await user.click(screen.getByRole('button', { name: '报告' }));
    await user.type(screen.getByLabelText('问题'), 'Top 3 目标值如何复算？');
    await user.click(screen.getByRole('button', { name: '提交问题' }));
    expect(screen.getByText('Top 3 目标值如何复算？')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '确认收到 v1.2' }));
    expect(screen.getByText('已确认接收 v1.2')).toBeTruthy();
  });

  it('invites a member and displays only a masked email', async () => {
    const user = userEvent.setup();
    const { container } = render(<Shell />);
    await user.click(screen.getByRole('button', { name: '成员' }));
    await user.type(screen.getByLabelText('姓名'), '周岚');
    await user.type(screen.getByLabelText('工作邮箱'), 'zhoulan@example.test');
    await user.selectOptions(screen.getByLabelText('项目角色'), 'member');
    await user.click(screen.getByRole('button', { name: '发送邀请' }));
    expect(screen.getByText('周岚')).toBeTruthy();
    expect(screen.getByText('z***@example.test')).toBeTruthy();
    expect(container.textContent).not.toContain('zhoulan@example.test');
  });
});
