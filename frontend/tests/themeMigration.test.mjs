import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import path from 'node:path';
import postcss from 'postcss';
import { build } from 'esbuild';

const project = fileURLToPath(new URL('..', import.meta.url));
const sourceRoot = path.join(project, 'src');
async function filesIn(dir) {
  const entries = await readdir(dir, { withFileTypes: true });
  return (await Promise.all(entries.map(entry => entry.isDirectory()
    ? filesIn(path.join(dir, entry.name)) : path.join(dir, entry.name)))).flat();
}
const sourceFiles = await filesIn(sourceRoot);

test('the source and dependency graph do not use the retired theme library or its layout adapter', async () => {
  const pkg = JSON.parse(await readFile(path.join(project, 'package.json'), 'utf8'));
  assert.equal(pkg.dependencies['@radix-ui/themes'], undefined);
  assert.equal(pkg.dependencies.cn, undefined);
  const retired = /@radix-ui\/themes|radix-themes|\.rt-[A-Z]|(?:ui\/layout|WorkspaceChrome\.css)/;
  for (const file of sourceFiles.filter(file => /\.(?:jsx?|css)$/.test(file))) {
    assert.doesNotMatch(await readFile(file, 'utf8'), retired, path.relative(project, file));
  }
  for (const file of sourceFiles.filter(file => file.endsWith('.jsx') && !file.includes('/ui/'))) {
    const source = await readFile(file, 'utf8');
    assert.doesNotMatch(source, /<(?:details|summary)\b/, `Use the standard Collapsible in ${path.relative(project, file)}`);
    assert.doesNotMatch(source, /from\s*['"]radix-ui['"]/, `Use the shared UI components in ${path.relative(project, file)}`);
  }
});

test('literal interface colors live in the theme; media pixel semantics and saved identity colors stay data', async () => {
  const literalColor = /#[\da-f]{3,8}\b|\b(?:rgba?|hsla?|oklch|oklab|lab|lch)\(/i;
  for (const file of sourceFiles.filter(file => file.endsWith('.css') && !file.endsWith('/theme.css'))) {
    assert.doesNotMatch(await readFile(file, 'utf8'), literalColor, path.relative(project, file));
  }
  for (const file of sourceFiles.filter(file => /\.jsx?$/.test(file))) {
    let source = await readFile(file, 'utf8');
    if (file.endsWith('/ReplacementStages.jsx')) {
      // These values are written into role/actor records, not applied as interface theme defaults.
      source = source.replace(/^const COLORS=\[[^\n]+$/m, '')
        .replace(/draft\.color\s*\|\|\s*['"]#7399ba['"]/g, 'draft.color');
    }
    if (file.endsWith('/SceneMaskEditor.jsx')) {
      // White/black define exported mask pixels and must not change with an interface theme.
      source = source.replace(/draw\.fillStyle\s*=\s*operation\.tool==='brush'\?'#fff':'#000';/, '');
    }
    assert.doesNotMatch(source, literalColor, path.relative(project, file));
    assert.doesNotMatch(source, /\b(?:bg|text|border|ring|fill|stroke)-(?:white|black|(?:red|blue|green|gray|slate|zinc|amber|indigo)-\d+)\b/,
      `Use semantic theme tokens in ${path.relative(project, file)}`);
  }
});

test('page CSS cannot repaint or resize standard controls', async () => {
  const uiSlot = /\[data-slot\s*=\s*["']?(?:button|input|textarea|native-select|select-trigger|tabs(?:-trigger|-list)?|badge|alert(?:-description)?|checkbox|radio-group(?:-item)?|slider(?:-thumb|-track|-range)?|switch|card(?:-content|-header|-title)?|dialog-content|alert-dialog-content|table(?:-cell|-head)?)\b/;
  const appearance = /^(?:color|background.*|border.*|box-shadow|font.*|text-shadow|outline.*|content)$/;
  const controlSize = /^(?:height|min-height|max-height|padding(?:-.+)?)$/;
  const failures = [];
  const disclosureClasses = new Set();
  for (const file of sourceFiles.filter(file => file.endsWith('.jsx') && !file.includes('/ui/'))) {
    const source = await readFile(file, 'utf8');
    for (const match of source.matchAll(/(?:<\w*CollapsibleContent\b[^>]*?className|\bcontentClassName)="([^"]*)"/g)) {
      for (const name of match[1].split(/\s+/)) disclosureClasses.add(name);
    }
  }
  for (const file of sourceFiles.filter(file => file.endsWith('.css') && !file.includes('/ui/') && !file.endsWith('/theme.css'))) {
    const root = postcss.parse(await readFile(file, 'utf8'), { from: file });
    root.walkRules(rule => {
      const targetsDisclosure = rule.selector.split(',').some(selector => {
        const last = selector.trim().replace(/\[[^\]]*\]/g, '').split(/[\s>+~]+/).at(-1);
        return [...disclosureClasses].some(name => last === `.${name}`);
      });
      if (targetsDisclosure) {
        let inComponentLayer = false;
        for (let parent = rule.parent; parent; parent = parent.parent) {
          if (parent.type === 'atrule' && parent.name === 'layer' && parent.params === 'components') inComponentLayer = true;
        }
        if (!inComponentLayer && rule.nodes.some(node => node.type === 'decl' && node.prop === 'display' && node.value !== 'none')) {
          failures.push(`${path.relative(project, file)}:${rule.source.start.line} Disclosure layout must stay below state visibility utilities`);
        }
      }
      const tableCell = /(?:^|[\s,>+~(])(?:th|td)(?=$|[\s,:>+~.#\[])/.test(rule.selector);
      if (!uiSlot.test(rule.selector) && !tableCell) return;
      for (const declaration of rule.nodes.filter(node => node.type === 'decl')) {
        const isTimelineGeometry = rule.selector.includes('.clip') && /^(?:height|min-height|max-height)$/.test(declaration.prop);
        const isViewportContainment = /dialog-content/.test(rule.selector) && declaration.prop === 'max-height'
          && /(?:dvh|svh|vh)/.test(declaration.value);
        if (appearance.test(declaration.prop) || (controlSize.test(declaration.prop) && !isTimelineGeometry && !isViewportContainment)) {
          failures.push(`${path.relative(project, file)}:${declaration.source.start.line} ${rule.selector}: ${declaration.prop}`);
        }
      }
    });
  }
  assert.deepEqual(failures, [], 'Control appearance belongs to components/ui; page CSS only owns business layout.');
});

const compiled = await build({
  stdin: {
    contents: `import React from 'react';
      import {renderToStaticMarkup} from 'react-dom/server';
      import {WorkspaceFrame} from './src/components/WorkspaceFrame.jsx';
      const steps=[['原片与分段','原片'],['人物替换','人物'],['场景与范围','场景'],['生成视频','生成'],['结果与导出','导出']];
      export function renderFrame(step){return renderToStaticMarkup(<WorkspaceFrame replacement
        steps={steps} step={step} projects={[]} configured onSelectProject={()=>{}}
        onStepChange={()=>{}} onOpenConfig={()=>{}} onNewProject={()=>{}} onOpenSeries={()=>{}}>
        <h1>工作区</h1></WorkspaceFrame>);}`,
    sourcefile: 'sidebar-contract.jsx',
    resolveDir: project,
    loader: 'jsx',
  },
  absWorkingDir: project, alias: { '@': sourceRoot }, jsx: 'automatic',
  bundle: true, write: false, platform: 'node', format: 'cjs', logLevel: 'silent',
});
const fixture = { exports: {} };
new Function('module', 'exports', 'require', compiled.outputFiles[0].text)(fixture, fixture.exports, createRequire(import.meta.url));

test('the standard sidebar keeps five named workflow actions and exactly one current step', () => {
  for (let step = 0; step < 5; step++) {
    const html = fixture.exports.renderFrame(step);
    assert.equal((html.match(/data-testid="workflow-step-\d"/g) || []).length, 5);
    assert.equal((html.match(/aria-current="step"/g) || []).length, 1);
    assert.match(html, /aria-label="制作流程"/);
    assert.match(html, /aria-label="API 设置"/);
    assert.match(html, /data-slot="sidebar-menu-button"/);
    assert.match(html, /aria-label="收起侧栏"[^>]*aria-expanded="true"/);
    assert.equal((html.match(/<main\b/g) || []).length, 1, 'The sidebar and workspace must not create nested main landmarks.');
    assert.doesNotMatch(html, /class="[^"]*workflow-step\b/);
  }
});
