import React, { useEffect, useRef, useState } from 'react';
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardAction } from "@/components/ui/card";
import { CheckCircle2 } from 'lucide-react';
import { SeriesDisclosure, SeriesNotice, SeriesSelect } from './SeriesControls';
import './SeriesBindings.css';

export default function SeriesBindings({ project, series, linked, onAction, busy }) {
  const actors = project?.source_actors || [];

  return (
    <div className="series-bindings">
      <div>
        <h2 className="font-semibold tracking-tight text-xl mb-2">{project?.name || '当前集'}的人物对应</h2>
        <p className="text-sm text-muted-foreground mb-0">
          为当前项目中的每个人选择“全剧角色 → 固定素材版本”，保存后再逐人确认。这里确认的是人物对应关系，镜头定位、替换范围和生成结果仍需分别审核。
        </p>
      </div>
      {!linked ? (
        <SeriesNotice>当前项目尚未关联到这部剧。请先到“集数总览”把当前项目登记为一集。</SeriesNotice>
      ) : actors.length === 0 ? (
        <SeriesNotice>请先返回当前项目，在人物替换步骤中添加原片人物，再回到这里绑定全剧角色。</SeriesNotice>
      ) : actors.map(actor => (
        <SeriesActorBinding key={`${project.id}-${actor.id}`} actor={actor} project={project} series={series} onAction={onAction} busy={busy} />
      ))}
    </div>
  );
}

export function SeriesActorBinding({ actor, project, series, onAction, busy }) {
  const existing = actor.series_binding;
  const [characterId, setCharacterId] = useState(existing?.character_id || '');
  const [versionNumber, setVersionNumber] = useState(existing?.version || '');
  const [dirty, setDirty] = useState(false);
  const signature = JSON.stringify(actor);
  const baseRevision = useRef(project.revision);
  const snapshot = useRef(signature);

  useEffect(() => {
    if (!dirty) {
      setCharacterId(existing?.character_id || '');
      setVersionNumber(existing?.version || '');
      baseRevision.current = project.revision;
      snapshot.current = signature;
    } else if (signature === snapshot.current) {
      baseRevision.current = project.revision;
    }
  }, [signature, project.revision, dirty, existing?.character_id, existing?.version]);

  const preserved = actor.scope === 'preserve';
  const characters = series.characters || [];
  const character = characters.find(item => item.id === characterId);
  const version = character?.versions.find(item => item.version === Number(versionNumber));
  const pinned = characters.find(item => item.id === existing?.character_id);
  const pinnedVersion = pinned?.versions.find(item => item.version === existing?.version);
  const newer = pinned?.versions.at(-1)?.version > existing?.version;
  const state = preserved ? '沿用原片' : existing?.confirmed ? '此人物对应已确认'
    : existing ? '此人物对应待确认' : '尚未绑定全剧角色';

  function selectCharacter(id) {
    setCharacterId(id);
    setVersionNumber(characters.find(item => item.id === id)?.versions.at(-1)?.version || '');
    setDirty(true);
  }

  function selectVersion(value) {
    setVersionNumber(Number(value));
    setDirty(true);
  }

  async function bind() {
    const result = await onAction('bind_actor', {
      actor_id: actor.id, character_id: characterId, version: Number(versionNumber),
      expected_project_revision: baseRevision.current,
    });
    if (result) setDirty(false);
  }

  return (
    <article data-testid={`series-binding-${actor.id}`}><Card className="series-binding-card">
<CardHeader>
          <div className="series-binding-identity">
            <CardTitle><h3 className="font-semibold tracking-tight text-lg mb-0">{actor.label}</h3></CardTitle>
            {actor.source_description && <CardDescription><p className="text-sm text-muted-foreground mt-2 mb-0">{actor.source_description}</p></CardDescription>}
          </div>
          <CardAction><Badge variant={preserved || existing?.confirmed ? 'secondary' : 'warning'}>{state}</Badge></CardAction>
        </CardHeader>
<CardContent>
        <SeriesDisclosure label="查看原片人物编号">
          <code className="series-binding-number font-mono text-sm text-muted-foreground">{actor.id}</code>
        </SeriesDisclosure>
        {preserved ? (
          <p className="text-sm text-muted-foreground mb-0">此人物保持原样，无需目标角色版本。</p>
        ) : <>
          <div className="series-binding-fields">
            <SeriesSelect label={`${actor.label}的全剧原角色`} ariaLabel={`${actor.id}全剧原角色`} value={characterId} onValueChange={selectCharacter} disabled={!!busy} options={[[ '', '请选择全剧原角色' ], ...characters.map(item => [item.id, `${item.name} · ${item.id}`])]} />
            <SeriesSelect label={`${actor.label}固定使用的素材版本`} ariaLabel={`${actor.id}固定素材版本`} value={versionNumber || ''} onValueChange={selectVersion} disabled={!!busy || !character} options={[[ '', '请选择版本' ], ...(character?.versions || []).map(item => [item.version, `v${item.version} · ${item.name}`])]} />
          </div>
          {version && <p className="series-binding-choice text-sm text-muted-foreground mb-0">
            将使用：{character.name} → {version.name}，固定 v{version.version} · {version.images.length} 张图片。
          </p>}
          <div className="flex">
            <Button type="button" onClick={bind} variant={dirty || !existing ? 'default' : 'outline'} size="lg" disabled={!!busy || !characterId || !versionNumber || (!dirty && !!existing)}>
              为{actor.label}保存全剧角色与固定版本
            </Button>
          </div>
          {existing && <div className="series-binding-pinned">
            <span className="text-sm font-bold">
              当前已保存：{pinned?.name || existing.character_id} → {pinnedVersion?.name || '素材待修复'} · v{existing.version}
            </span>
            {newer && <SeriesNotice tone="warning">
              角色库已有新版本，本集继续固定 v{existing.version}。需要升级时在上方明确选择并重新确认。
            </SeriesNotice>}
            <p className="text-sm text-muted-foreground mb-0">
              对照本集原片，确认这条人物对应关系。确认后仍需在项目中检查各镜头的位置、遮挡和动作。
            </p>
            <div className="flex flex-wrap gap-3">
              <Button type="button" onClick={() => onAction('confirm_binding', { actor_id: actor.id, approved: true })} size="lg" disabled={!!busy || dirty || existing.confirmed}>
                <CheckCircle2 size={16} aria-hidden="true" />确认{actor.label}对应此全剧原角色
              </Button>
              {existing.confirmed && <Button type="button" onClick={() => onAction('confirm_binding', { actor_id: actor.id, approved: false })} variant="outline" size="lg" disabled={!!busy || dirty}>
                撤销{actor.label}的本集身份确认
              </Button>}
            </div>
          </div>}
        </>}
      </CardContent>
</Card></article>
  );
}
