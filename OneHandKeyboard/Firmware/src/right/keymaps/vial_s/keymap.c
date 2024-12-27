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
        {0, {KC_Q, KC_NO, KC_V, KC_NO, 200}},	//��1:Q�� , ��2:V��
        {1, {KC_R, KC_NO, KC_Z, KC_NO, 200}},	//��1:R�� , ��2:Z��
        
        {2, {KC_NO, KC_NO, KC_NO, KC_NO, 200}},
        {3, {KC_NO, KC_NO, KC_NO, KC_NO, 200}},
        {4, {KC_NO, KC_NO, KC_NO, KC_NO, 200}},
        
        {5, {KC_DOT, KC_NO, KC_COMMA, KC_NO, 200}},	//��1:. , ��2:,
        {6, {KC_SPC, MO(1), KC_NO, KC_NO, 200}},	//��1:Space , Ȧ��:MO(1)
        {7, {KC_LBRC, KC_NO, KC_RBRC, KC_NO, 200}},	//��1:[ , ��2:]
        {8, {KC_NO, KC_NO, KC_NO, KC_NO, 200}},
        {9, {KC_NO, KC_NO, KC_NO, KC_NO, 200}}
    };

    for (int i = 0; i < sizeof(td_entries) / sizeof(td_entries[0]); i++) {
        dynamic_keymap_set_tap_dance(td_entries[i].index, &td_entries[i].entry);
    }
}
#endif

//TO(0): �ѱ�, TO(1): ����, TO(2): ��Ÿ 
const uint16_t PROGMEM keymaps[][MATRIX_ROWS][MATRIX_COLS] = {
    [0] = LAYOUT_onekey(
        KC_1,	KC_2,	KC_3,	KC_4,	KC_5,
        TO(2),	TD(0),	KC_W,	KC_D,	TD(1),	KC_G,	KC_BSPC,
        KC_RALT,	KC_S,	IN,	ARAE_A,	JI,	KC_T,	MO(3),
        TD(5),	KC_X,	KC_E,	KC_A,	KC_F,	KC_C,	KC_ENT,
        TD(6),	KC_LSFT,	KC_LCTL
    ),
    [1] = LAYOUT_onekey(
        KC_6,	KC_7,	KC_8,	KC_9,	KC_0,
        KC_ESC,	KC_Y,	KC_U,	KC_I,	KC_O,	KC_MINS,	KC_GRV,
        KC_TAB,	KC_H,	KC_J,	KC_K,	KC_P,	KC_EQL,	TD(7),
        KC_CAPS,	KC_B,	KC_N,	KC_SCLN,	KC_QUOT,	KC_SLSH,	KC_BSLS,
        KC_TRNS,	KC_TRNS,	KC_LGUI
    ),
    [2] = LAYOUT_onekey(
        KC_PSLS,		KC_PAST,	KC_PMNS,	KC_PPLS	,	KC_NUM,
        TO(0),	KC_NO,	KC_P7,	KC_P8,	KC_P9,	KC_NO,	KC_BSPC,
        KC_LALT,	KC_NO,	KC_P4,	KC_P5,	KC_P6,	KC_NO,	KC_NO,
        KC_TAB,	KC_P0,	KC_P1,	KC_P2,	KC_P3,	KC_PDOT,	KC_PENT	,
        KC_SPC,	KC_LSFT,	KC_LCTL
    ),

    [3] = LAYOUT_onekey(
        KC_F1, KC_F2, KC_F3, KC_F4, KC_F5,
        KC_TRNS, KC_F6, KC_F7, KC_F8, KC_F9, KC_F10, KC_TRNS,
        KC_TRNS, KC_F11, KC_F12, KC_INS, KC_HOME, KC_PGUP, KC_TRNS,
        KC_TRNS, KC_NO, KC_NO, KC_DEL, KC_END, KC_PGDN, KC_TRNS,
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
            case TD(0):	case TD(1):	case TD(2):	case TD(3):	case TD(4):
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
