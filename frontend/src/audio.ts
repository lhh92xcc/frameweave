export const speechDefaults = { speed: 1, vol: 1, pitch: 0, emotion: null };
export interface SavedVoice {
  id: string;
  name: string;
  provider: string;
  voice_id: string;
  profile: { voice_id: string; speed: number; vol: number; pitch: number; emotion?: string };
  locked: boolean;
  activated: boolean;
  preview_url: string;
  source_project_id: string;
  source_role_id: string;
  review: string;
}
export const emotionOptions = [
  { id: "happy", name: "高兴" },
  { id: "sad", name: "悲伤" },
  { id: "angry", name: "愤怒" },
  { id: "fearful", name: "害怕" },
  { id: "disgusted", name: "厌恶" },
  { id: "surprised", name: "惊讶" },
  { id: "calm", name: "平静" },
];
// Starter IDs from MiniMax's published system voice catalogue; availability is checked with the account.
export const starterVoices = [
  { id: "cute_boy", name: "可爱男童" },
  { id: "clever_boy", name: "聪明男童" },
  { id: "lovely_girl", name: "萌萌女童" },
  { id: "Chinese (Mandarin)_Cute_Spirit", name: "憨憨萌兽" },
  { id: "male-qn-qingse", name: "青涩青年" },
  { id: "male-qn-daxuesheng", name: "青年大学生" },
  { id: "female-chengshu", name: "成熟女性" },
  { id: "Chinese (Mandarin)_Gentleman", name: "温润男声" },
  { id: "Chinese (Mandarin)_Humorous_Elder", name: "搞笑大爷" },
];
