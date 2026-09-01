/**
 * `markdown-it` 与 `crypto-js` 的最小类型声明。
 *
 * 两个包都是纯 JS 发行、类型放在独立的 `@types/*` 包里。本机装不到那两个 `@types`
 * （离线环境),于是 `vue-tsc` 对每个 import 报 TS7016,并把回调参数全推成隐式 any
 * （TS7006）—— 一共 22 条,是当前类型错误里最大的一族。
 *
 * 这里只声明**仓库真正用到的那部分接口**,不追求覆盖上游全量 API：
 * 声明面越小,和真实包对不上的风险越低。等能装 `@types/markdown-it` 和
 * `@types/crypto-js` 时,删掉本文件即可 —— 真正的类型包会自动接管,
 * 且比这里更严格。
 *
 * 用到的位置：
 *   markdown-it — `ConfigurationList.vue`、`IMAdapterDetail.vue`
 *   crypto-js   — `ConfigurationList.vue`(密码字段哈希)
 */

declare module 'markdown-it' {
  /** 一个 markdown token。仓库只在 link_open 规则里透传它,不读具体字段,除了 attrSet。 */
  interface MarkdownItToken {
    attrSet(name: string, value: string): void
    [key: string]: unknown
  }

  interface MarkdownItRenderer {
    rules: Record<string, RenderRule | undefined>
    renderToken(tokens: MarkdownItToken[], idx: number, options: unknown): string
  }

  type RenderRule = (
    tokens: MarkdownItToken[],
    idx: number,
    options: unknown,
    env: unknown,
    self: MarkdownItRenderer
  ) => string

  class MarkdownIt {
    constructor(options?: Record<string, unknown>)
    renderer: MarkdownItRenderer
    render(src: string): string
  }

  export default MarkdownIt
  export type { MarkdownItToken, MarkdownItRenderer, RenderRule }
}

declare module 'crypto-js' {
  /** 哈希结果。仓库只调用 `toString(encoder)`。 */
  interface WordArray {
    toString(encoder?: Encoder): string
  }

  interface Encoder {
    stringify(wordArray: WordArray): string
  }

  interface CryptoJSStatic {
    enc: { Hex: Encoder; Utf8: Encoder; Base64: Encoder }
    /**
     * 按算法名动态取哈希函数（`CryptoJS[hashFunc]`）。
     *
     * 第二个参数是 HMAC 系列的密钥；非 HMAC 算法不传。签名放宽到可选,是为了容纳
     * 这一处动态调用 —— 收得更紧会让合法调用报错。
     */
    [algorithm: string]: unknown
  }

  const CryptoJS: CryptoJSStatic
  export default CryptoJS
  export type { WordArray, Encoder }
}
