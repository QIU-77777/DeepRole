import type { MapId } from "./game";

const SCENE_TONES: Record<MapId, [number, number]> = {
  campus_center: [174.61, 261.63],
  arts_hallway: [146.83, 220],
  clubroom: [220, 329.63],
  rooftop: [196, 293.66],
};

/** A tiny local-only ambience layer; it starts only after an explicit player input. */
export class SpatialAmbience {
  private context: AudioContext | null = null;
  private gains: GainNode[] = [];
  private oscillators: OscillatorNode[] = [];
  private scene: MapId = "campus_center";
  private enabled = true;

  setEnabled(enabled: boolean) {
    this.enabled = enabled;
    if (!enabled) {
      this.stop();
    } else if (this.context?.state === "running") {
      this.start();
    }
  }

  setScene(scene: MapId) {
    if (this.scene === scene) return;
    this.scene = scene;
    if (this.context?.state === "running" && this.enabled) this.start();
  }

  unlock() {
    if (!this.enabled) return;
    if (!this.context) this.context = new AudioContext();
    void this.context.resume().then(() => this.start());
  }

  dispose() {
    this.stop();
    void this.context?.close();
    this.context = null;
  }

  private start() {
    if (!this.context || this.context.state !== "running") return;
    this.stop();
    const [root, fifth] = SCENE_TONES[this.scene];
    for (const [frequency, level, type] of [
      [root, 0.012, "sine"],
      [fifth, 0.006, "triangle"],
    ] as const) {
      const oscillator = this.context.createOscillator();
      const gain = this.context.createGain();
      oscillator.type = type;
      oscillator.frequency.value = frequency;
      gain.gain.value = level;
      oscillator.connect(gain).connect(this.context.destination);
      oscillator.start();
      this.oscillators.push(oscillator);
      this.gains.push(gain);
    }
  }

  private stop() {
    for (const gain of this.gains) gain.gain.setTargetAtTime(0, this.context?.currentTime ?? 0, 0.03);
    for (const oscillator of this.oscillators) oscillator.stop();
    this.gains = [];
    this.oscillators = [];
  }
}
