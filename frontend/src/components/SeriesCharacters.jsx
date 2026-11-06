import React, { useId, useState } from 'react';
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardAction } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { Separator } from "@/components/ui/separator";
import { Plus } from 'lucide-react';
import { assetUrl } from '../api';
import { SeriesDisclosure, SeriesField, SeriesNotice, SeriesSelect } from './SeriesControls';
import './SeriesCharacters.css';

const aliasList = text => text.split(/[\n,，、]/).map(alias => alias.trim()).filter(Boolean);

function PublishCharacter({ project, series, linked, onAction, busy }) {
  const actors = (project?.source_actors || []).filter(actor => actor.scope !== 'preserve');
  const [actorId, setActorId] = useState(actors[0]?.id || '');
  const [characterId, setCharacterId] = useState('');
  const [name, setName] = useState('');
  const [aliases, setAliases] = useState('');
  const actor = actors.find(item => item.id === actorId);
  const role = project?.roles?.find(item => item.id === actor?.target_role_id);
  const characters = series.characters || [];
  const character = characters.find(item => item.id === characterId);

  async function publish() {
    const result = await onAction('publish_character', {
      actor_id: actorId,
      character_id: characterId || undefined,
      name: characterId ? undefined : name.trim() || undefined,
      aliases: characterId ? undefined : aliasList(aliases),
    });
    if (result) {
      setName('');
      setAliases('');
    }
  }

  return (
    <section aria-labelledby="series-publish-character-title"><Card className="series-publish-character">
<CardHeader>
          <CardTitle><h2 id="series-publish-character-title" className="font-semibold tracking-tight text-xl mb-0">将当前项目的造型保存为全剧固定版本</h2></CardTitle>
          <CardDescription><p className="text-sm text-muted-foreground mb-0">
            先在当前项目的「人物替换」中绑定新角色参考图，再保存为全剧可共用的造型版本。发布后，到「当前项目人物对应」选择该版本并逐人确认。
          </p></CardDescription>
        </CardHeader>
<CardContent>
        {!linked ? <SeriesNotice tone="warning">请先把当前项目登记到这部剧，再发布角色版本。</SeriesNotice> : <>
          <div className="series-publish-fields">
            <SeriesSelect label="提供角色素材的本集人物" value={actorId} onValueChange={setActorId} disabled={!!busy} options={[['', '请选择本集人物'], ...actors.map(item => [item.id, `${item.label} · ${item.id}`])]} />
            <SeriesSelect label="全剧角色版本保存方式" value={characterId} onValueChange={setCharacterId} disabled={!!busy} options={[['', '建立新的全剧原角色'], ...characters.map(item => [item.id, `为「${item.name}」追加新素材版本`])]} />
          </div>
          {!characterId ? (
            <div className="series-publish-fields">
              <SeriesField label="原剧角色名称（固定身份）" ariaLabel="全剧原角色名称" maxLength={100} value={name} onChange={event => setName(event.target.value)} disabled={!!busy} placeholder="原剧中的角色名或可辨认身份" />
              <SeriesField label="原角色别名／阶段说明" ariaLabel="全剧原角色别名" value={aliases} onChange={event => setAliases(event.target.value)} disabled={!!busy} placeholder="逗号分隔，例如：少年时期、别名" />
            </div>
          ) : <p className="text-sm text-muted-foreground mb-0">
            追加到固定原角色 <span className="font-medium text-muted-foreground">{character?.name}</span>，沿用原角色的名称、编号和别名。
          </p>}
          <div className="series-publish-summary">
            <span className="text-sm font-medium">本次目标造型 · {role?.name || '尚未绑定目标角色'}</span>
            <Badge variant="secondary">{role?.images?.length || 0} 张已保存图片</Badge>
          </div>
          {characterId && <SeriesNotice>
            这会追加一个独立版本，已有集数继续使用原版本。需要升级的集数由你逐集选择、重新绑定并确认。
          </SeriesNotice>}
          <div className="flex">
            <Button type="button" onClick={publish} size="lg" disabled={(busy === 'publish_character') || (!!busy || !actorId || !role?.images?.length || (!characterId && !name.trim()))} aria-busy={(busy === 'publish_character') || undefined}>{(busy === 'publish_character') && <Spinner />}
              <Plus size={16} aria-hidden="true" />
              {characterId ? '发布新的固定素材版本' : '建立全剧角色与首个素材版本'}
            </Button>
          </div>
        </>}
      </CardContent>
</Card></section>
  );
}

export function SeriesCharacterCard({ character }) {
  const titleId = useId();
  const versions = character.versions || [];
  const [versionNumber, setVersionNumber] = useState(versions.at(-1)?.version?.toString());
  const version = versions.find(item => String(item.version) === versionNumber) || versions.at(-1);

  return (
    <article aria-labelledby={titleId}><Card className="series-character-profile">
<CardHeader>
          <CardTitle><h3 id={titleId} className="font-semibold tracking-tight text-xl mb-0">{character.name}</h3></CardTitle>
          <CardAction><SeriesSelect label={`${character.name}素材版本`} ariaLabel={`${character.id}素材版本`} value={version?.version ?? ''} onValueChange={setVersionNumber} disabled={!versions.length} placeholder="尚无固定素材版本" options={versions.map(item => [item.version, `固定版本 v${item.version}`])} /></CardAction>
        </CardHeader>
<CardContent>
        {(character.aliases?.length > 0 || character.source_description) && (
          <div className="flex flex-col gap-2">
            {character.aliases?.length > 0 && <p className="text-sm text-muted-foreground mb-0">别名／阶段 · {character.aliases.join('、')}</p>}
            {character.source_description && <p className="text-sm text-muted-foreground mb-0">{character.source_description}</p>}
          </div>
        )}
        <SeriesDisclosure label="查看固定编号">
          <code className="series-character-fixed-id font-mono text-sm text-muted-foreground">{character.id}</code>
        </SeriesDisclosure>
        {version && <>
          <Separator decorative />
          <div className="flex justify-between items-center gap-3 flex-wrap">
            <span className="text-sm font-bold">目标造型 · {version.name || '未命名'}</span>
            <Badge variant="secondary">固定版本 v{version.version}</Badge>
          </div>
          {version.images?.length > 0 && <div className="series-character-images">
            {version.images.map(asset => (
              <a key={asset.id || asset.sha256} href={assetUrl(asset)} target="_blank" rel="noreferrer" className="series-character-image text-primary underline-offset-4 hover:underline">
                <img src={assetUrl(asset)} loading="lazy" alt={`${character.name} v${version.version} · ${asset.name}`} />
              </a>
            ))}
          </div>}
          {version.description && <p className="text-sm text-muted-foreground mb-0">{version.description}</p>}
          <p className="text-sm text-muted-foreground mb-0">此版本已冻结。新增素材通过发布新版本保存，已关联集数继续使用各自的固定版本。</p>
        </>}
      </CardContent>
</Card></article>
  );
}

export default function SeriesCharacters({ project, series, linked, onAction, busy }) {
  return (
    <div className="series-characters-tab">
      <PublishCharacter key={`${project?.id || ''}:${series.id}`} project={project} series={series} linked={linked} onAction={onAction} busy={busy} />
      <section className="series-character-library" aria-labelledby="series-character-library-title">
        <div className="flex flex-col gap-2">
          <h2 id="series-character-library-title" className="font-semibold tracking-tight text-xl mb-0">全剧原角色档案</h2>
          <p className="text-sm text-muted-foreground mb-0">同一个原剧角色使用同一固定编号。新版本独立保存，各集继续使用各自固定的造型。</p>
        </div>
        {series.characters?.length > 0 ? (
          <div className="series-character-library-grid">
            {series.characters.map(character => <SeriesCharacterCard key={character.id} character={character} />)}
          </div>
        ) : <SeriesNotice>当前没有全剧角色。关联一集原片，绑定目标图片后，从上方发布第一份角色素材。</SeriesNotice>}
      </section>
    </div>
  );
}
