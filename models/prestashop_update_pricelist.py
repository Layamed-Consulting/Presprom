from odoo import models, fields, api, _
from odoo.exceptions import UserError
import requests
import time
import base64
import xml.etree.ElementTree as ET
import logging

_logger = logging.getLogger(__name__)


class PrestashopApplyPromospecifique(models.Model):
    _name = 'prestashop.apply.promospecifique'
    _description = 'PrestaShop Apply Promo Specifique'

    id_prestashop_product = fields.Integer(
        string="Id Produit PrestaShop",
        readonly=True,
        help="ID du produit parent dans PrestaShop"
    )
    reference = fields.Char(
        string="Référence",
        required=True,
        help="Référence du produit/variante"
    )
    reduction = fields.Float(
        string="Réduction (%)",
        required=True,
        help="Pourcentage de réduction (ex: 0.20 pour 20%)"
    )
    date_from = fields.Datetime(
        string="Date Début",
        required=True,
        default=fields.Datetime.now
    )
    date_to = fields.Datetime(
        string="Date Fin",
        help="Laisser vide pour aucune date de fin"
    )
    is_done = fields.Boolean(
        string="Terminé",
        default=False,
        readonly=True
    )
    is_synchronised = fields.Boolean(
        string="Synchronisé",
        default=False,
        readonly=True,
        help="Indique si la promotion a été appliquée avec succès"
    )
    specific_price_id = fields.Integer(
        string="ID Prix Spécifique",
        readonly=True,
        help="ID du prix spécifique créé dans PrestaShop"
    )
    error_message = fields.Text(
        string="Message d'erreur",
        readonly=True
    )
    promotion_id = fields.Char(
        string="ID Promotion(s)", required=True,
        help="ID(s) de catégorie promotion séparés par virgule. Ex: 256,245,125"
    )

    def action_get_combination_id(self):
        """Get combination ID from PrestaShop by reference"""
        self.ensure_one()

        if not self.reference:
            raise UserError("Référence manquante!")

        _logger.info(f"Searching product for reference: {self.reference}")

        try:
            # Get products filtered by reference
            response = requests.get(
                "https://www.premiumshop.ma/api/products",
                auth=("E93WGT9K8726WW7F8CWIXDH9VGFBLH6A", ""),
                params={
                    'filter[reference]': self.reference,
                    'display': 'full'
                },
                timeout=60
            )

            if response.status_code != 200:
                error_msg = f"Erreur API: {response.status_code}"
                _logger.error(error_msg)
                self.error_message = error_msg
                raise UserError(error_msg)

            # Parse XML response
            root = ET.fromstring(response.content)

            # Find product
            product = root.find('.//product')

            if product is None:
                error_msg = f"Aucun produit trouvé avec la référence: {self.reference}"
                _logger.error(error_msg)
                self.error_message = error_msg
                raise UserError(error_msg)

            # Extract product ID
            product_id = product.find('id')

            if product_id is not None:
                self.write({
                    'id_prestashop_product': int(product_id.text),
                    'is_done': True,
                    'error_message': False
                })

                _logger.info(f"Product found - ID: {product_id.text}")

                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Succès!',
                        'message': f'Produit trouvé!\nID: {product_id.text}',
                        'type': 'success',
                        'sticky': False,
                    }
                }
            else:
                error_msg = "ID de produit introuvable"
                self.error_message = error_msg
                raise UserError(error_msg)

        except Exception as e:
            error_msg = f"Exception: {str(e)}"
            _logger.error(error_msg)
            self.error_message = error_msg
            raise UserError(error_msg)

    def action_get_combination_id_queue(self):
        """Queue job wrapper for getting combination ID - Process in batches of 100"""
        total_records = len(self)
        batch_size = 100

        # Process in batches
        for i in range(0, total_records, batch_size):
            batch = self[i:i + batch_size]
            batch_number = (i // batch_size) + 1
            total_batches = (total_records + batch_size - 1) // batch_size

            batch.with_delay(
                description=f"Get combination IDs - Batch {batch_number}/{total_batches} ({len(batch)} records)",
                priority=5
            )._job_get_combination_id_batch()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Tâches en file d\'attente',
                'message': f'{total_records} enregistrement(s) ajouté(s) en {(total_records + batch_size - 1) // batch_size} batch(s) de {batch_size}',
                'type': 'info',
                'sticky': False,
            }
        }

    def _job_get_combination_id_batch(self):
        """Job method to process a batch of combination IDs"""
        success_count = 0
        failed_count = 0

        for record in self:
            try:
                _logger.info(f"🔄 BATCH JOB: Getting product ID for reference: {record.reference}")
                record.action_get_combination_id()
                success_count += 1
                _logger.info(f"✅ Success ({success_count}/{len(self)}): {record.reference}")
            except Exception as e:
                failed_count += 1
                _logger.error(f"❌ Failed ({failed_count}/{len(self)}): {record.reference} - {str(e)}")
                record.write({'error_message': f"Batch job failed: {str(e)}"})
                # Don't raise - continue processing other records in batch

        _logger.info(f"📊 BATCH COMPLETE: {success_count} succeeded, {failed_count} failed out of {len(self)} records")

    def action_apply_specific_price(self):
        """Apply specific price (promotion) to combination in PrestaShop"""
        self.ensure_one()
        return self._apply_specific_price_internal()

    def _check_specific_price_exists(self, product_id):
        """Check if a specific price already exists for this product"""
        try:
            _logger.info(f"🔍 Checking if specific price exists for product {product_id}...")

            response = requests.get(
                "https://www.premiumshop.ma/api/specific_prices",
                auth=("E93WGT9K8726WW7F8CWIXDH9VGFBLH6A", ""),
                params={
                    'filter[id_product]': product_id,
                    'display': 'full'
                },
                timeout=60
            )

            if response.status_code == 200:
                root = ET.fromstring(response.content)
                specific_prices = root.findall('.//specific_price')

                if specific_prices:
                    _logger.info(f"✅ Found {len(specific_prices)} existing specific price(s) for product {product_id}")
                    return True
                else:
                    _logger.info(f"✅ No existing specific prices for product {product_id}")
                    return False
            else:
                _logger.warning(f"⚠️ Could not check specific prices: {response.status_code}")
                return False

        except Exception as e:
            _logger.error(f"❌ Error checking specific price: {str(e)}")
            return False

    def _apply_specific_price_internal(self):
        """Internal method - does the actual work without ensure_one"""
        if not self.id_prestashop_product or self.id_prestashop_product == 0:
            raise UserError("ID de produit manquant!")

        if self.reduction <= 0 or self.reduction > 1:
            raise UserError("La réduction doit être entre 0 et 1 (ex: 0.20 pour 20%)")

        # Check if specific price already exists for this product
        if self._check_specific_price_exists(self.id_prestashop_product):
            _logger.info(f"⚠️ Specific price already exists for product {self.id_prestashop_product}, skipping...")
            self.write({
                'is_done': True,
                'is_synchronised': True,
                'error_message': 'Prix spécifique déjà existant pour ce produit'
            })
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Déjà Existant',
                    'message': f'Un prix spécifique existe déjà pour le produit {self.id_prestashop_product}',
                    'type': 'warning',
                    'sticky': False,
                }
            }

        _logger.info(f"Applying specific price for combination {self.id_prestashop_product}")

        try:
            # Format dates
            date_from_str = self.date_from.strftime('%Y-%m-%d %H:%M:%S') if self.date_from else '0000-00-00 00:00:00'
            date_to_str = self.date_to.strftime('%Y-%m-%d %H:%M:%S') if self.date_to else '0000-00-00 00:00:00'

            # Build XML for specific price
            xml_data = f"""<?xml version="1.0" encoding="UTF-8"?>
    <prestashop xmlns:xlink="http://www.w3.org/1999/xlink">
      <specific_price>
        <id_shop_group><![CDATA[0]]></id_shop_group>
        <id_shop><![CDATA[1]]></id_shop>
        <id_cart><![CDATA[0]]></id_cart>
        <id_product><![CDATA[{self.id_prestashop_product}]]></id_product>
        <id_product_attribute><![CDATA[0]]></id_product_attribute>
        <id_currency><![CDATA[0]]></id_currency>
        <id_country><![CDATA[0]]></id_country>
        <id_group><![CDATA[0]]></id_group>
        <id_customer><![CDATA[0]]></id_customer>
        <id_specific_price_rule><![CDATA[0]]></id_specific_price_rule>
        <price><![CDATA[-1.000000]]></price>
        <from_quantity><![CDATA[1]]></from_quantity>
        <reduction><![CDATA[{self.reduction:.6f}]]></reduction>
        <reduction_tax><![CDATA[1]]></reduction_tax>
        <reduction_type><![CDATA[percentage]]></reduction_type>
        <from><![CDATA[{date_from_str}]]></from>
        <to><![CDATA[{date_to_str}]]></to>
      </specific_price>
    </prestashop>"""

            _logger.info(f"📤 Sending specific price data...")

            # POST to PrestaShop
            response = requests.post(
                "https://www.premiumshop.ma/api/specific_prices",
                auth=("E93WGT9K8726WW7F8CWIXDH9VGFBLH6A", ""),
                headers={"Content-Type": "application/xml"},
                data=xml_data.encode('utf-8'),
                timeout=30
            )

            if response.status_code in [200, 201]:
                # Parse response to get specific_price ID
                root = ET.fromstring(response.content)
                specific_price_id = root.find('.//specific_price/id')

                if specific_price_id is not None:
                    # Add "Promotion" category to product
                    promo_category_added = self._add_promotion_category_to_product(self.id_prestashop_product)

                    self.write({
                        'specific_price_id': int(specific_price_id.text),
                        'is_done': True,
                        'error_message': False
                    })

                    _logger.info(f"✅ Specific price created with ID: {specific_price_id.text}")

                    success_msg = (f'Prix spécifique créé avec succès!\n'
                                   f'ID: {specific_price_id.text}\n'
                                   f'Réduction: {self.reduction * 100:.0f}%\n'
                                   f'Référence: {self.reference}')

                    if promo_category_added:
                        success_msg += '\n✅ Catégorie "Promotion" ajoutée'
                    else:
                        success_msg += '\n⚠️ Catégorie "Promotion" non ajoutée'

                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': 'Promotion Appliquée!',
                            'message': success_msg,
                            'type': 'success',
                            'sticky': True,
                        }
                    }
                else:
                    error_msg = "⚠️ Prix spécifique créé mais ID non récupéré"
                    self.write({
                        'is_done': True,
                        'error_message': error_msg
                    })
                    _logger.warning(error_msg)
            else:
                error_msg = f"❌ Erreur lors de la création du prix spécifique: {response.status_code}\n{response.text}"
                _logger.error(error_msg)
                self.error_message = error_msg
                raise UserError(error_msg)

        except Exception as e:
            error_msg = f"Exception: {str(e)}"
            _logger.error(error_msg)
            self.error_message = error_msg
            raise UserError(error_msg)

    def action_apply_specific_price_queue(self):
        """Queue job wrapper for applying specific price - Process in batches of 100"""
        # Filter records that have product ID
        valid_records = self.filtered(lambda r: r.id_prestashop_product and r.id_prestashop_product > 0)
        skipped_count = len(self) - len(valid_records)

        if skipped_count > 0:
            _logger.warning(f"⚠️ Skipping {skipped_count} record(s) without product ID")

        if not valid_records:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Aucun enregistrement valide',
                    'message': 'Tous les enregistrements sélectionnés n\'ont pas d\'ID de produit',
                    'type': 'warning',
                    'sticky': False,
                }
            }

        total_records = len(valid_records)
        batch_size = 100

        # Process in batches
        for i in range(0, total_records, batch_size):
            batch = valid_records[i:i + batch_size]
            batch_number = (i // batch_size) + 1
            total_batches = (total_records + batch_size - 1) // batch_size

            batch.with_delay(
                description=f"Apply specific prices - Batch {batch_number}/{total_batches} ({len(batch)} records)",
                priority=5
            )._job_apply_specific_price_batch()

        message = f'{total_records} enregistrement(s) ajouté(s) en {(total_records + batch_size - 1) // batch_size} batch(s) de {batch_size}'
        if skipped_count > 0:
            message += f'\n⚠️ {skipped_count} enregistrement(s) ignoré(s)'

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Tâches en file d\'attente',
                'message': message,
                'type': 'info',
                'sticky': False,
            }
        }

    def _job_apply_specific_price_batch(self):
        """Job method to process a batch of specific prices"""
        success_count = 0
        failed_count = 0
        skipped_count = 0

        for record in self:
            try:
                _logger.info(f"🔄 BATCH JOB: Applying specific price for reference: {record.reference}")

                # Check if specific price already exists
                if record._check_specific_price_exists(record.id_prestashop_product):
                    skipped_count += 1
                    _logger.info(
                        f"⏭️ Skipped ({skipped_count}/{len(self)}): {record.reference} - Already has specific price")
                    record.write({
                        'is_done': True,
                        'is_synchronised': True,
                        'error_message': 'Prix spécifique déjà existant pour ce produit'
                    })
                    continue

                # Call internal method instead of action_apply_specific_price
                record._apply_specific_price_internal()
                success_count += 1
                _logger.info(f"✅ Success ({success_count}/{len(self)}): {record.reference}")
            except Exception as e:
                failed_count += 1
                _logger.error(f"❌ Failed ({failed_count}/{len(self)}): {record.reference} - {str(e)}")
                record.write({'error_message': f"Batch job failed: {str(e)}"})
                # Don't raise - continue processing other records in batch

        _logger.info(
            f"📊 BATCH COMPLETE: {success_count} succeeded, {skipped_count} skipped, {failed_count} failed out of {len(self)} records")

    def _add_promotion_category_to_product(self, product_id):
        """Add Promotion category(ies) to product WITHOUT breaking data"""
        try:
            _logger.info(f"Adding Promotion category(ies) to product {product_id}")

            # ── Parse multiple promotion IDs ──
            promo_category_ids = {
                str(pid).strip()
                for pid in str(self.promotion_id).split(',')
                if str(pid).strip()
            }

            _logger.info(f"Promotion categories to add: {promo_category_ids}")

            # GET product (FULL SAFE DATA)
            get_product = requests.get(
                f"https://www.premiumshop.ma/api/products/{product_id}",
                auth=("E93WGT9K8726WW7F8CWIXDH9VGFBLH6A", ""),
                timeout=60
            )

            if get_product.status_code != 200:
                return False

            root = ET.fromstring(get_product.content)
            product_data = root.find('product')

            # --- REQUIRED / CRITICAL FIELDS ---
            price = product_data.findtext('price')
            default_category = product_data.findtext('id_category_default')
            reference = product_data.findtext('reference')
            ean13 = product_data.findtext('ean13')
            active = product_data.findtext('active')
            visibility = product_data.findtext('visibility')
            available_for_order = product_data.findtext('available_for_order')
            id_manufacturer = product_data.findtext('id_manufacturer')
            id_tax_rules_group = product_data.findtext('id_tax_rules_group')

            # --- ADDITIONAL FIELDS TO PRESERVE ---
            cache_default_attribute = product_data.findtext('cache_default_attribute') or '0'
            location = product_data.findtext('location') or ''
            state = product_data.findtext('state') or '1'
            product_type = product_data.findtext('product_type') or 'standard'
            minimal_quantity = product_data.findtext('minimal_quantity') or '1'
            redirect_type = product_data.findtext('redirect_type') or '404'
            show_price = product_data.findtext('show_price') or '1'

            # --- MULTILINGUAL FIELDS ---
            description_nodes = product_data.findall('description/language')
            description_short_nodes = product_data.findall('description_short/language')
            name_nodes = product_data.findall('name/language')
            link_nodes = product_data.findall('link_rewrite/language')

            # ── Get existing categories ──
            categories = product_data.findall('.//associations/categories/category')
            existing_ids = {
                c.findtext('id') for c in categories if c.find('id') is not None
            }

            # ── Check if ALL promo categories already assigned ──
            if promo_category_ids.issubset(existing_ids):
                _logger.info(f"All promotion categories already assigned to product {product_id}")
                return True

            # ── Merge existing + all promo IDs ──
            final_ids = existing_ids | promo_category_ids
            _logger.info(f"Final categories for product {product_id}: {final_ids}")

            # BUILD SAFE PUT XML
            ET.register_namespace('xlink', 'http://www.w3.org/1999/xlink')

            prestashop = ET.Element('prestashop')
            product = ET.SubElement(prestashop, 'product')

            # --- REQUIRED ---
            ET.SubElement(product, 'id').text = str(product_id)
            ET.SubElement(product, 'price').text = price
            ET.SubElement(product, 'reference').text = reference
            ET.SubElement(product, 'ean13').text = ean13
            ET.SubElement(product, 'active').text = active
            ET.SubElement(product, 'visibility').text = visibility
            ET.SubElement(product, 'available_for_order').text = available_for_order
            ET.SubElement(product, 'id_category_default').text = default_category
            ET.SubElement(product, 'id_manufacturer').text = id_manufacturer
            ET.SubElement(product, 'id_tax_rules_group').text = id_tax_rules_group

            # --- PRESERVED ---
            ET.SubElement(product, 'cache_default_attribute').text = cache_default_attribute
            ET.SubElement(product, 'location').text = location
            ET.SubElement(product, 'state').text = state
            ET.SubElement(product, 'product_type').text = product_type
            ET.SubElement(product, 'minimal_quantity').text = minimal_quantity
            ET.SubElement(product, 'redirect_type').text = redirect_type
            ET.SubElement(product, 'show_price').text = show_price

            # --- MULTILINGUAL ---
            name_el = ET.SubElement(product, 'name')
            for lang in name_nodes:
                ET.SubElement(name_el, 'language', {'id': lang.get('id')}).text = lang.text

            desc_el = ET.SubElement(product, 'description')
            for lang in description_nodes:
                ET.SubElement(desc_el, 'language', {'id': lang.get('id')}).text = lang.text

            desc_short_el = ET.SubElement(product, 'description_short')
            for lang in description_short_nodes:
                ET.SubElement(desc_short_el, 'language', {'id': lang.get('id')}).text = lang.text

            link_el = ET.SubElement(product, 'link_rewrite')
            for lang in link_nodes:
                ET.SubElement(link_el, 'language', {'id': lang.get('id')}).text = lang.text

            # --- CATEGORIES ---
            associations = ET.SubElement(product, 'associations')
            cats = ET.SubElement(associations, 'categories')
            for cid in final_ids:
                cat = ET.SubElement(cats, 'category')
                ET.SubElement(cat, 'id').text = cid

            xml_data = ET.tostring(prestashop, encoding='utf-8')

            update_response = requests.put(
                f"https://www.premiumshop.ma/api/products/{product_id}",
                auth=("E93WGT9K8726WW7F8CWIXDH9VGFBLH6A", ""),
                headers={"Content-Type": "application/xml"},
                data=xml_data,
                timeout=60
            )

            if update_response.status_code == 200:
                _logger.info(f"✅ Promotion categories {promo_category_ids} added safely to product {product_id}")
                return True

            _logger.error(update_response.text)
            return False

        except Exception as e:
            _logger.error(f"Exception: {str(e)}", exc_info=True)
            return False
