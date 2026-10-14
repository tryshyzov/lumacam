package com.lumalink.companion

import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Typeface
import android.graphics.drawable.Drawable
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.os.Build
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.accessibility.AccessibilityNodeInfo
import android.widget.LinearLayout
import android.widget.TextView

/** The palette of the desktop app (measured from the prototype). */
internal object Palette {
    val CHROME = Color.rgb(0x62, 0x62, 0x62)
    val FRAME = Color.rgb(0x3d, 0x3d, 0x3d)
    val SHEET = Color.rgb(0x3d, 0x3d, 0x3d)
    val TEXT = Color.WHITE
    val TEXT_SECONDARY = Color.rgb(0xe0, 0xe0, 0xe0)
    val TEXT_ON_SOLID = Color.rgb(0x2b, 0x2b, 0x2b)
    val BORDER_CONTROL = Color.rgb(0xb8, 0xb8, 0xb8)
    val SCRIM = Color.argb(128, 0, 0, 0)
    val TAG = Color.argb(128, 0, 0, 0)
    val PRESS_ON_DARK = Color.argb(36, 255, 255, 255)
    val PRESS_ON_LIGHT = Color.argb(36, 0, 0, 0)
}

internal fun Context.dp(value: Float): Int =
    TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, value, resources.displayMetrics).toInt()

internal fun Context.dp(value: Int): Int = dp(value.toFloat())

internal fun semiBold(): Typeface =
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
        Typeface.create(Typeface.DEFAULT, 600, false)
    } else {
        Typeface.DEFAULT_BOLD
    }

internal fun rounded(fill: Int, radius: Float, stroke: Int? = null, strokeWidth: Int = 0): GradientDrawable =
    GradientDrawable().apply {
        setColor(fill)
        cornerRadius = radius
        if (stroke != null) setStroke(strokeWidth, stroke)
    }

/** ``content`` with the platform's touch ripple on top, clipped to its shape. */
internal fun pressable(content: Drawable, pressColor: Int): RippleDrawable =
    RippleDrawable(ColorStateList.valueOf(pressColor), content, content.constantState?.newDrawable()?.mutate())

/** Options in a row; the chosen one is filled white, the rest are outlined text. */
internal class SegmentedControl(
    context: Context,
    private val values: List<String>,
    itemHeightDp: Int = 28,
    private val textSizeSp: Float = 13f,
) : LinearLayout(context) {
    var onChange: ((String) -> Unit)? = null
    var value: String = values.first()
        private set
    private val items = values.map { option ->
        TextView(context).apply {
            text = option
            gravity = Gravity.CENTER
            textSize = textSizeSp
            maxLines = 1
            isClickable = true
            isFocusable = true
            setOnClickListener { select(option, notify = true) }
        }
    }

    init {
        orientation = HORIZONTAL
        val inset = context.dp(2)
        setPadding(inset, inset, inset, inset)
        background = rounded(Color.TRANSPARENT, context.dp(6).toFloat(), Palette.BORDER_CONTROL, context.dp(1))
        items.forEachIndexed { index, item ->
            addView(
                item,
                LayoutParams(0, context.dp(itemHeightDp), 1f).apply {
                    if (index > 0) marginStart = context.dp(2)
                },
            )
        }
        refresh()
    }

    fun select(option: String, notify: Boolean = false) {
        if (option == value || option !in values) return
        value = option
        refresh()
        if (notify) onChange?.invoke(option)
    }

    fun setLabels(labels: Map<String, String>) {
        values.forEachIndexed { index, option -> labels[option]?.let { items[index].text = it } }
    }

    override fun setEnabled(enabled: Boolean) {
        super.setEnabled(enabled)
        alpha = if (enabled) 1f else 0.45f
        items.forEach { it.isEnabled = enabled }
    }

    private fun refresh() {
        val radius = context.dp(4).toFloat()
        values.forEachIndexed { index, option ->
            val item = items[index]
            val chosen = option == value
            item.isSelected = chosen
            item.setTextColor(if (chosen) Palette.TEXT_ON_SOLID else Palette.TEXT)
            item.typeface = if (chosen) semiBold() else Typeface.DEFAULT
            item.background = pressable(
                rounded(if (chosen) Palette.TEXT else Color.TRANSPARENT, radius),
                if (chosen) Palette.PRESS_ON_LIGHT else Palette.PRESS_ON_DARK,
            )
        }
    }
}

/** An on/off switch drawn like the desktop app's: outlined when off, filled when on. */
internal class ToggleView(context: Context) : View(context) {
    var isOn = true
        set(on) {
            field = on
            invalidate()
        }
    var onToggle: ((Boolean) -> Unit)? = null
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val rect = RectF()

    init {
        isClickable = true
        isFocusable = true
        setOnClickListener {
            isOn = !isOn
            onToggle?.invoke(isOn)
        }
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        setMeasuredDimension(context.dp(36), context.dp(20))
    }

    override fun onDraw(canvas: Canvas) {
        val stroke = context.dp(1).toFloat()
        rect.set(stroke / 2, stroke / 2, width - stroke / 2, height - stroke / 2)
        val radius = rect.height() / 2
        paint.style = Paint.Style.FILL
        paint.color = if (isOn) Palette.TEXT else Color.TRANSPARENT
        canvas.drawRoundRect(rect, radius, radius, paint)
        paint.style = Paint.Style.STROKE
        paint.strokeWidth = stroke
        paint.color = if (isOn) Palette.TEXT else Palette.BORDER_CONTROL
        canvas.drawRoundRect(rect, radius, radius, paint)
        paint.style = Paint.Style.FILL
        paint.color = if (isOn) Palette.TEXT_ON_SOLID else Palette.BORDER_CONTROL
        val knob = context.dp(6).toFloat()
        val centerX = if (isOn) width - height / 2f else height / 2f
        canvas.drawCircle(centerX, height / 2f, knob, paint)
    }

    override fun onInitializeAccessibilityNodeInfo(info: AccessibilityNodeInfo) {
        super.onInitializeAccessibilityNodeInfo(info)
        info.className = "android.widget.Switch"
        info.isCheckable = true
        info.isChecked = isOn
    }
}
