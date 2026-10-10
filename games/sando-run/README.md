# 参道ラン（3D）

夏祭りの夜の参道を水風船が転がる 3D ランゲーム。three.js r128（cdnjs）を使っています。

```
open games/sando-run/index.html   # ブラウザで開くだけ（three.js は CDN から読み込み）
```

- 3 レーン。`←` `→` / 左右スワイプでレーン移動、`↑` `Space` / タップでジャンプ
- 樽は飛び越えられる。屋台の荷車は高いので、左右によけるしかない
- 金魚 1 匹 50 点（黄金は 5 匹分）＋ 進んだ距離。だんだん速くなる

`game.html` は Artifact 公開用。`index.html` は `games/wrap.sh games/sando-run` で生成。
