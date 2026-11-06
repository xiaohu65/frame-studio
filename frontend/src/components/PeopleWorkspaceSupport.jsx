import React from 'react';

import { MediaPreview, SegmentList, Timeline } from './MediaWorkspace';
import './PeopleWorkspaceSupport.css';

export function PeopleWorkspaceReference({ project, segment, mode, setMode, onSelect, currentTime, onTime }) {
  return (
    <div className="flex flex-col gap-6 people-support-reference">
      <section aria-labelledby="people-reference-segments-title">
        <h3 id="people-reference-segments-title" className="font-semibold leading-snug tracking-tight text-lg mb-3">当前片段</h3>
        <SegmentList
          sourceUi
          project={project}
          segments={project.segments || []}
          selectedId={segment?.id}
          onSelect={onSelect}
        />
      </section>
      <MediaPreview compact sourceUi project={project} segment={segment} mode={mode} setMode={setMode} onTime={onTime} />
      <Timeline sourceUi project={project} selectedId={segment?.id} onSelect={onSelect} currentTime={currentTime} />
    </div>
  );
}

