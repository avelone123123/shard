import React from "react";
import { Composition } from "remotion";
import { ShardPromo } from "./ShardPromo";
import timing from "./timing.json";
export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="ShardPromo"
      component={ShardPromo}
      durationInFrames={timing.totalFrames}
      fps={timing.fps}
      width={timing.width}
      height={timing.height}
    />
  );
};
