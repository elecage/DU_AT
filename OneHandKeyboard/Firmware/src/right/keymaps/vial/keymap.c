/* SPDX-License-Identifier: GPL-2.0-or-later */

#include QMK_KEYBOARD_H
#include "vial.h"
#include "dynamic_keymap.h"
#include "onekey.h"

enum custom_keycodes{
	IN = QK_KB_0,
	ARAE_A,
	JI
};

#ifdef VIAL_ENABLE
void keyboard_post_init_user(void) {
    struct {
        uint8_t index;
        vial_tap_dance_entry_t entry;
    } td_entries[] = {
        {0, {KC_A, KC_NO, KC_SCLN, KC_NO, 200}},	//��1:�� , ��2:;	**
        {1, {KC_F, KC_NO, KC_QUOT, KC_NO, 200}},	//��1:�� , ��2:'	**
        {2, {KC_C, KC_NO, KC_SLSH, KC_NO, 200}},	//��1:�� , ��2:/	**
        {3, {KC_L, KC_NO, KC_QUOT, KC_NO, 200}},	//��1:L , ��2:'
        {4, {KC_M, KC_NO, KC_SLSH, KC_NO, 200}},	//��1:M , ��2:/
        
        {5, {KC_DOT, KC_NO, KC_COMMA, KC_NO, 200}},	//��1:. , ��2:,
        {6, {KC_MINS, KC_NO, KC_EQL, KC_NO, 200}},	//��1:- , ��2:=
        {7, {KC_LBRC, KC_NO, KC_RBRC, KC_NO, 200}},	//��1:[ , ��2:]
        {8, {KC_GRV, KC_NO, KC_NUBS, KC_NO, 200}},	//��1:` , ��2:'\'
        {9, {KC_G, KC_NO, RSFT(KC_1), KC_NO, 200}},	//��1:�� , ��2:!	**
        
        {10, {KC_G, KC_NO, KC_Q, KC_NO, 200}},	//��1:G , ��2:Q
        {11, {KC_V, KC_NO, KC_Z, KC_NO, 200}},	//��1:V , ��2:Z
        {12, {KC_R, KC_NO, KC_P, KC_NO, 200}},	//��1:R , ��2:P
        {13, {KC_H, KC_NO, KC_B, KC_NO, 200}},	//��1:H , ��2:B
        {14, {KC_N, KC_NO, KC_F, KC_NO, 200}},	//��1:N , ��2:F
        {15, {KC_U, KC_NO, KC_C, KC_NO, 200}},	//��1:U , ��2:C
        {16, {KC_I, KC_NO, KC_J, KC_NO, 200}},	//��1:I , ��2:J
        {17, {KC_D, KC_NO, KC_K, KC_NO, 200}},	//��1:D , ��2:K
        {18, {KC_W, KC_NO, KC_X, KC_NO, 200}},	//��1:W , ��2:X
        {19, {KC_Y, KC_NO, KC_SCLN, KC_NO, 200}},	//��1:V , ��2:;
        
        {20, {KC_1, KC_NO, KC_F1, KC_NO, 200}},	//��1:1 , ��2:F1
        {21, {KC_2, KC_NO, KC_F2, KC_NO, 200}},	//��1:2 , ��2:F2
        {22, {KC_3, KC_NO, KC_F3, KC_NO, 200}},	//��1:3 , ��2:F3
        {23, {KC_4, KC_NO, KC_F4, KC_NO, 200}},	//��1:4 , ��2:F4
        {24, {KC_5, KC_NO, KC_F5, KC_NO, 200}},	//��1:5 , ��2:F5
        {25, {KC_6, KC_NO, KC_F6, KC_NO, 200}},	//��1:6 , ��2:F6
        {26, {KC_7, KC_NO, KC_F7, KC_NO, 200}},	//��1:7 , ��2:F7
        {27, {KC_8, KC_NO, KC_F8, KC_NO, 200}},	//��1:8 , ��2:F8
        {28, {KC_9, KC_NO, KC_F9, KC_NO, 200}},	//��1:9 , ��2:F9
        {29, {KC_0, KC_NO, KC_F10, KC_NO, 200}},	//��1:0 , ��2:F10
        
        {30, {KC_S, KC_NO, RSFT(KC_1), KC_NO, 200}},	//��1:S , ��2:!
        
        {31, {KC_DEL, KC_NO, KC_INS, KC_NO, 200}},	//��1:Del , ��2:Ins
        {32, {KC_RALT, KC_NO, KC_LALT, KC_NO, 200}},	//��1:RALT , ��2:LALT
        {33, {KC_LCTL, KC_NO, KC_LGUI, KC_NO, 200}}	//��1:LCTL , ��2:Win
    };

    for (int i = 0; i < sizeof(td_entries) / sizeof(td_entries[0]); i++) {
        dynamic_keymap_set_tap_dance(td_entries[i].index, &td_entries[i].entry);
    }
}
#endif

//TO(0): �ѱ�, TO(1): ����, TO(2): ��Ÿ 
const uint16_t PROGMEM keymaps[][MATRIX_ROWS][MATRIX_COLS] = {
    [0] = LAYOUT_onekey(
        KC_ESC,  TD(32),   KC_V,    KC_Z,    TD(6),
        TO(2),   KC_Q,      KC_W,    KC_D,    KC_R,    TD(9),    KC_BSPC,
        TO(1),   KC_S,      IN,		 ARAE_A,  JI,      KC_T,    TD(5),
        TO(0),   KC_X,      KC_E,    TD(0),   TD(1),   TD(2),   KC_ENT,
        KC_SPC,  KC_LSFT,   TD(33)
    ),
    [1] = LAYOUT_onekey(
        KC_ESC,  TD(32),  TD(10),   TD(11),  TD(6),
        TO(2),   TD(12),    TD(13),  TD(14),  KC_T,    TD(30),    KC_BSPC,
        TO(1),   TD(15),    KC_A,    KC_E,    KC_O,    TD(16),  TD(5),
        TO(0),   TD(17),    TD(18),  TD(19),  TD(3),   TD(4),  KC_TRNS,
        KC_SPC,  KC_LSFT,   TD(33)
    ),
    [2] = LAYOUT_onekey(
        TD(20),  TD(21),    TD(22),  TD(23),  TD(24),
        TO(2),   TD(25),    TD(26),  TD(27),  TD(28),  TD(29),  KC_BSPC,
        TO(1),   KC_HOME,   TD(7),   KC_UP,   TD(8),   KC_PGUP, TD(31),
        TO(0),   KC_END,    KC_LEFT, KC_DOWN, KC_RGHT, KC_PGDN, KC_TRNS,
        KC_SPC,  KC_LSFT,   TD(33)
    ),

    [3] = LAYOUT_onekey(
        KC_TRNS, KC_TRNS, KC_TRNS, KC_TRNS, KC_TRNS,
        KC_TRNS, KC_TRNS, KC_TRNS, KC_TRNS, KC_TRNS, KC_TRNS, KC_TRNS,
        KC_TRNS, KC_TRNS, KC_TRNS, KC_TRNS, KC_TRNS, KC_TRNS, KC_TRNS,
        KC_TRNS, KC_TRNS, KC_TRNS, KC_TRNS, KC_TRNS, KC_TRNS, KC_TRNS,
        KC_TRNS, KC_TRNS, KC_TRNS
    )
};

// ���¸� �����ϱ� ���� ���� ���� ����
static bool l_pressed = false;  // l�� ���ȴ��� ���θ� ����
static bool k_replaced = false; // k�� ��ü�ߴ��� ���θ� ����
static bool i_replaced = false; // i�� ��ü�ߴ��� ���θ� ����
static bool m_pressed = false;  // m�� ���ȴ��� ���θ� ����
static bool n_replaced = false; // n�� ��ü�ߴ��� ���θ� ����
static bool consonant = false;  // ���� ���� ���θ� ����
static uint8_t dot_count = 0;   // �� ī��Ʈ
static bool j_replaced = false; // j�� ��ü�ߴ��� ���θ� ����
static bool u_replaced = false; // u�� ��ü�ߴ��� ���θ� ����
static bool b_replaced = false; // b�� ��ü�ߴ��� ���θ� ����
static bool nj_replaced = false; // nj�� ��ü�ߴ��� ���θ� ����

// ���¸� �ʱ�ȭ�ϴ� �Լ�
void reset_state(void) {
    l_pressed = false;
    k_replaced = false;
    m_pressed = false;
    n_replaced = false;
    i_replaced = false;
    j_replaced = false;
    u_replaced = false;
    b_replaced = false;
    nj_replaced = false;
    consonant = false;
    dot_count = 0;  // arae_a ī��Ʈ �ʱ�ȭ
}

bool process_record_user(uint16_t keycode, keyrecord_t *record) {
    if (record->event.pressed) {
        switch (keycode) {
            case IN:
                if (k_replaced) {
                    // i�� ����� o�� ��� (��)
                    tap_code(KC_BSPC);      // �齺���̽��� i�� ����
                    tap_code(KC_O);         // o�� ���
                    reset_state();          // ���� �ʱ�ȭ
                    return false;           // l�� ��µ��� �ʵ��� ��
                } else if (i_replaced) {
                    // i�� ����� O�� ��� (��)
                    tap_code(KC_BSPC);          // �齺���̽��� i�� ����
                    register_code(KC_LSFT);     // Shift�� ���� ����
                    tap_code(KC_O);             // o�� ���
                    unregister_code(KC_LSFT);   // Shift�� �� ����
                    reset_state();              // ���� �ʱ�ȭ
                    return false;
                } else if (consonant && dot_count == 1) {
                    // j�� ��� (��)
                    tap_code(KC_J);             // j�� ���
                    reset_state();              // ���� �ʱ�ȭ
                    j_replaced = true;
                    return false;
                } else if (consonant && dot_count == 2) {
                    // u�� ��� (��)
                    tap_code(KC_U);             // u�� ���
                    reset_state();              // ���� �ʱ�ȭ
                    u_replaced = true;
                    return false;
                } else if (j_replaced) {
                    // p�� ��� (��)
                    tap_code(KC_BSPC);
                    tap_code(KC_P);             // p�� ���
                    reset_state();              // ���� �ʱ�ȭ
                    return false;
                } else if (u_replaced) {
                    // P�� ��� (��)
                    tap_code(KC_BSPC);
                    register_code(KC_LSFT);     // Shift�� ���� ����
                    tap_code(KC_P);             // p�� ���
                    unregister_code(KC_LSFT);   // Shift�� �� ����
                    reset_state();              // ���� �ʱ�ȭ
                    return false;
                } else if(!b_replaced && !nj_replaced && dot_count == 0) {
                    // l�� ��� (��)
                    tap_code(KC_L);             // l�� ���
                    l_pressed = true;           // l ���� true
                    return false;                // l�� ����ϵ��� ��
                }else if (b_replaced) {
                    // b�� ����� nj�� ��� (��)
                    tap_code(KC_BSPC);          // �齺���̽��� b�� ����
                    tap_code(KC_N);             // n�� ���
                    tap_code(KC_J);             // j�� ���
                    reset_state();              // ���� �ʱ�ȭ
                    nj_replaced = true;         // l ���� true
                    return false;
                }else if (nj_replaced) {
                    // i�� ����� O�� ��� (��)
                    tap_code(KC_BSPC);          // �齺���̽��� j�� ����
                    tap_code(KC_P);             // p�� ���
                    reset_state();              // ���� �ʱ�ȭ
                    return false;
                }
                break;

            case ARAE_A:
                if (l_pressed && !k_replaced) {
                    // l�� ����� k�� ��� (��)
                    tap_code(KC_BSPC);          // �齺���̽��� l�� ����
                    tap_code(KC_K);             // k�� ���
                    k_replaced = true;          // k ���� true
                    dot_count = 0;              // �� �ʱ�ȭ
                    return false;
                } else if (l_pressed && k_replaced) {
                    // k�� ����� i�� ��� (��)
                    tap_code(KC_BSPC);          // �齺���̽��� k�� ����
                    tap_code(KC_I);             // i�� ���
                    reset_state();              // ���� �ʱ�ȭ
                    i_replaced = true;          // i ���� true
                    return false;
                } else if (m_pressed && !n_replaced) {
                    // m�� ����� n�� ��� (��)
                    tap_code(KC_BSPC);          // �齺���̽��� m�� ����
                    tap_code(KC_N);             // n�� ���
                    n_replaced = true;          // n ���� true
                    dot_count = 0;              // �� �ʱ�ȭ
                    return false;
                } else if (m_pressed && n_replaced) {
                    // n�� ����� b�� ��� (��)
                    tap_code(KC_BSPC);          // �齺���̽��� n�� ����
                    tap_code(KC_B);             // b�� ���
                    reset_state();              // ���� �ʱ�ȭ
                    b_replaced = true;          // b ���� true
                    return false;
                } else if (consonant) {
            		dot_count++;
				    return false;
				    
                } else if (dot_count == 3) {
                    // �� ī��Ʈ �ʱ�ȭ
                    dot_count = 0;
                    consonant =  false;
                    return false;
                }
                break;

            case JI:
                // m�� ��� (��)
                if (dot_count == 0) {
                	tap_code(KC_M);             // m�� ���
                    m_pressed = true;           // m ���� true
                    return false;                // m�� ����ϵ��� ��
                } else if (consonant && dot_count == 1) {
                    // h�� ��� (��)
                    tap_code(KC_H);             // h�� ���
                    reset_state();              // ���� �ʱ�ȭ
                    return false;
                } else if (consonant && dot_count == 2) {
                    // y�� ��� (��)
                    tap_code(KC_Y);             // y�� ���
                    reset_state();              // ���� �ʱ�ȭ
                    return false;
                }
                break;
				// ���� Ű�ڵ�
            case KC_Q: case KC_W: case KC_E: case KC_R: case KC_T: 
            case KC_A: case KC_S: case KC_D: case KC_F: case KC_G: 
            case KC_Z: case KC_X: case KC_C: case KC_V:
            	//�ѱ� ������ �Ǵ� 
            case TD(0):	case TD(1):	case TD(2):	case TD(9):
                // ������ ������ ���¸� �ʱ�ȭ
                reset_state();
                consonant = true;               // ���� ���� true
                break;
			
			case KC_BSPC:
				reset_state();
				break;
				
            default:
                // �ٸ� Ű�� ������ ��� �ʱ�ȭ
                reset_state();
                break;
        }
    }
    return true; // �⺻ Ű ó���� ��� ����
}
