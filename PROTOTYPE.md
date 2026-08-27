# AutoVocal Prototype

这是一个临时验证版：使用一条干净的参考人声，对用户人声进行自动音高修正。

## 当前能力

- Windows 桌面 GUI（PySide6）
- 参考人声 / 用户人声 WAV 拖放
- Praat autocorrelation F0 分析
- octave-invariant pitch-class + energy DTW 对齐
- nearest-octave 目标音高生成
- 修音强度可调
- 保留用户 vibrato / pitch expression
- Praat PSOLA overlap-add 重建
- 无 CUDA、无模型下载、无 FFmpeg 运行时依赖

## 推荐输入

优先使用 10–30 秒的干净单人声 WAV。参考和用户应演唱同一段歌词/旋律。这个版本暂时不做伴奏人声分离，也不做歌词级 forced alignment。

## 本地开发

```bash
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
python app.py
```

## Windows EXE

推送到 `prototype/reference-vocal-autotune` 会触发 GitHub Actions。构建产物名为 `AutoVocal-Prototype-Windows`，其中包含 `AutoVocal-Prototype.exe`。

## 原型算法

```text
reference.wav -> Praat F0 --┐
                            +-> pitch-class/energy DTW -> reference pitch on user timeline
user.wav ------> Praat F0 --┘                               |
                                                            v
                                             nearest octave target
                                                            |
                                user base pitch + preserved expression
                                                            |
                                                      Praat PSOLA
                                                            |
                                              user_autovocal.wav
```

## 下一步

如果听感验证通过，优先把 F0 检测替换成 RMVPE/ONNX；随后加入 BS-RoFormer 人声分离和 phoneme-aware alignment。UI 与 target-curve / resynthesis 接口可以保持不变。
